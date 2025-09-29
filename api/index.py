# api/index.py
# Backend FastAPI para Vercel (ASGI). Usa:
# - Groq (LLM) → generación de itinerario/chat
# - Open-Meteo → clima (sin API key)
# - SerpAPI → lugares/POIs para enriquecer el itinerario
#
# Rutas públicas (Vercel las sirve con prefijo /api):
#   GET  /health
#   POST /chat
#   GET  /weather
#   POST /itinerary
#   POST /download/txt
#   POST /download/ics

import json
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, validator

# Groq (LLM)
try:
    from groq import Groq
except Exception:
    Groq = None  # type: ignore

app = FastAPI(title="Travel Agent API (Groq+SerpAPI)", version="2.0.0")

# ----- CORS -----
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # en prod puedes restringir tu dominio
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ====== MODELOS ======
class UserKeys(BaseModel):
    groq_api_key: str = Field(..., min_length=10)
    serpapi_api_key: Optional[str] = Field(None, min_length=10)

class ChatMessage(BaseModel):
    role: str
    content: str

class TripPrefs(BaseModel):
    location: str
    days: int = Field(..., ge=1, le=14)
    start_date: Optional[str] = None
    language: str = Field("es")

    @validator("start_date", pre=True)
    def _validate_start_date(cls, v):
        if v in (None, "", "today", "mañana", "tomorrow"):
            return None
        datetime.fromisoformat(v)  # lanza si es inválida
        return v

class ChatRequest(BaseModel):
    keys: UserKeys
    message: str
    history: List[ChatMessage] = []
    prefs: Optional[TripPrefs] = None

class WeatherDay(BaseModel):
    date: str
    code: int
    summary: str
    temp_max: float
    temp_min: float
    precipitation_sum: float

class ItineraryDay(BaseModel):
    date: str
    title: str
    morning: str
    afternoon: str
    evening: str
    notes: Optional[str] = ""

class Itinerary(BaseModel):
    location: str
    days: List[ItineraryDay]
    weather_overview: str

# ====== CONSTANTES ======
WMO_CODE_MAP = {
    0: "despejado/soleado", 1: "mayormente despejado", 2: "parcialmente nublado", 3: "nublado",
    45: "niebla", 48: "niebla escarchada",
    51: "llovizna ligera", 53: "llovizna moderada", 55: "llovizna densa",
    56: "llovizna helada ligera", 57: "llovizna helada densa",
    61: "lluvia ligera", 63: "lluvia moderada", 65: "lluvia intensa",
    66: "lluvia helada ligera", 67: "lluvia helada intensa",
    71: "nieve ligera", 73: "nieve moderada", 75: "nieve intensa",
    77: "granizo", 80: "chubascos ligeros", 81: "chubascos moderados", 82: "chubascos fuertes",
    85: "chubascos de nieve ligeros", 86: "chubascos de nieve fuertes",
    95: "tormenta", 96: "tormenta con granizo ligera", 99: "tormenta con granizo fuerte",
}

# ====== UTILIDADES (CLIMA) ======
async def geocode_city(city: str) -> Dict:
    url = "https://geocoding-api.open-meteo.com/v1/search"
    params = {"name": city, "count": 1, "language": "es", "format": "json"}
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    if not data.get("results"):
        raise HTTPException(status_code=404, detail=f"No se encontró la ciudad: {city}")
    hit = data["results"][0]
    return {
        "name": hit.get("name"),
        "country": hit.get("country"),
        "lat": hit["latitude"],
        "lon": hit["longitude"],
        "timezone": hit.get("timezone", "auto"),
    }

async def fetch_weather(lat: float, lon: float, tz: str, start: date, days: int) -> List[WeatherDay]:
    end = start + timedelta(days=days - 1)
    params = {
        "latitude": lat, "longitude": lon,
        "daily": "weathercode,precipitation_sum,temperature_2m_max,temperature_2m_min",
        "timezone": tz or "auto",
        "start_date": start.isoformat(), "end_date": end.isoformat(),
    }
    url = "https://api.open-meteo.com/v1/forecast"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        d = r.json()

    daily = d.get("daily", {})
    times = daily.get("time", [])
    out: List[WeatherDay] = []
    for i, day_iso in enumerate(times):
        code = int(daily["weathercode"][i])
        out.append(WeatherDay(
            date=day_iso,
            code=code,
            summary=WMO_CODE_MAP.get(code, ""),
            temp_max=float(daily["temperature_2m_max"][i]),
            temp_min=float(daily["temperature_2m_min"][i]),
            precipitation_sum=float(daily.get("precipitation_sum", [0.0]*len(times))[i]),
        ))
    return out

def summarize_weather(days: List[WeatherDay]) -> str:
    if not days:
        return "Sin datos meteorológicos."
    total_rain = sum(d.precipitation_sum for d in days)
    if total_rain >= 5:
        overall = "lluvioso"
    elif any(d.code in (2, 3, 45, 48) for d in days):
        overall = "nublado"
    else:
        overall = "soleado"
    mn = min(d.temp_min for d in days)
    mx = max(d.temp_max for d in days)
    return f"Panorama general: {overall}. Temperaturas entre {mn:.0f}°C y {mx:.0f}°C. Lluvia acumulada aprox. {total_rain:.1f} mm."

# ====== UTILIDADES (SERPAPI) ======
async def serpapi_search(api_key: Optional[str], q: str, location: str, num: int = 6) -> List[Dict]:
    """Devuelve una listita de {title, link, snippet} usando SerpAPI (Google). Si falta API key → []."""
    if not api_key:
        return []
    url = "https://serpapi.com/search.json"
    params = {
        "engine": "google",
        "q": f"{q} in {location}",
        "api_key": api_key,
        "num": max(3, min(10, num)),
        "hl": "es",
        "gl": "es",
    }
    async with httpx.AsyncClient(timeout=25) as client:
        r = await client.get(url, params=params)
        if r.status_code != 200:
            return []
        data = r.json()
    results = []
    for item in (data.get("organic_results") or [])[:num]:
        results.append({
            "title": item.get("title", "")[:120],
            "link": item.get("link", ""),
            "snippet": item.get("snippet", "")[:200],
        })
    return results

# ====== AGENTES ======
class WeatherAgent:
    @staticmethod
    async def get(city: str, start: Optional[str], n_days: int) -> Dict:
        geo = await geocode_city(city)
        start_date = datetime.fromisoformat(start).date() if start else (date.today() + timedelta(days=1))
        days = await fetch_weather(geo["lat"], geo["lon"], geo["timezone"], start_date, n_days)
        return {"geo": geo, "days": [d.dict() for d in days], "overview": summarize_weather(days)}

def _safe_json_extract(text: str) -> Dict:
    """Intenta cargar JSON. Si el modelo devolvió texto extra, recorta desde la 1ª { … } válida."""
    try:
        return json.loads(text)
    except Exception:
        pass
    # Busca el primer bloque {...}
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end+1])
        except Exception:
            return {}
    return {}

class PlannerAgent:
    @staticmethod
    async def plan_itinerary(keys: UserKeys, city: str, weather: Dict, n_days: int, language: str) -> Itinerary:
        if Groq is None:
            raise HTTPException(status_code=500, detail="Groq SDK no disponible en el entorno.")
        client = Groq(api_key=keys.groq_api_key)

        # Enriquecer con POIs via SerpAPI (si hay key)
        pois = await serpapi_search(keys.serpapi_api_key, "top attractions", city, num=6)
        eats = await serpapi_search(keys.serpapi_api_key, "best local restaurants", city, num=4)

        # Hints de clima por día
        hints = []
        for d in weather.get("days", []):
            hint = ""
            if d["precipitation_sum"] >= 2:
                hint = "(día lluvioso: prioriza museos, mercados cubiertos, tours bajo techo)"
            elif d["code"] in (0, 1):
                hint = "(día soleado: parques, miradores y actividades al aire libre)"
            hints.append(f"{d['date']}: {WMO_CODE_MAP.get(d['code'], '')} {hint}")
        guide_text = "\n".join(hints)

        schema_text = """
Devuelve SOLO un JSON válido con esta estructura exacta:
{
  "location": "string",
  "days": [
    { "date":"YYYY-MM-DD", "title":"string", "morning":"string", "afternoon":"string", "evening":"string", "notes":"string opcional" }
  ],
  "weather_overview": "string"
}
No incluyas nada fuera del JSON.
"""

        pois_txt = ""
        if pois:
            pois_txt = "Sugerencias (POIs):\n" + "\n".join([f"- {p['title']}" for p in pois])
        eats_txt = ""
        if eats:
            eats_txt = "Comida local (ideas):\n" + "\n".join([f"- {e['title']}" for e in eats])

        sys = (
            "Eres un agente de viajes práctico y realista. Planifica actividades cercanas entre sí, "
            "incluye sugerencias de transporte (a pie/transporte público), menciona comida local y alternativas si llueve. "
            "NO inventes precios ni reservas. Tu salida DEBE ser JSON exactamente como se te pide."
        )
        user = f"""Ciudad: {city}
Días: {n_days}
Idioma de la respuesta: {language}
Resumen del clima: {weather.get('overview','')}
Guía por día:
{guide_text}

{pois_txt}

{eats_txt}

{schema_text}
"""

        completion = client.chat.completions.create(
            model="llama-3.1-70b-versatile",
            messages=[
                {"role": "system", "content": sys},
                {"role": "user", "content": user},
            ],
            temperature=0.4,
            max_tokens=2000,
        )

        content = completion.choices[0].message.content if completion.choices else "{}"
        data = _safe_json_extract(content)
        if not data:
            raise HTTPException(status_code=500, detail="No se pudo construir el JSON del itinerario.")
        return Itinerary(**data)

class Orchestrator:
    @staticmethod
    async def handle(req: ChatRequest) -> Dict:
        text = (req.message or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="Mensaje vacío")
        if len(text) > 4000:
            text = text[:4000]

        t = text.lower()
        wants_plan = any(k in t for k in ["itinerario", "plan", "viaje"]) or (req.prefs is not None)
        wants_weather = any(k in t for k in ["clima", "tiempo", "lluvia", "soleado"]) or (req.prefs is not None)

        if req.prefs:
            prefs = req.prefs
            weather = await WeatherAgent.get(prefs.location, prefs.start_date, prefs.days)
            itinerary = await PlannerAgent.plan_itinerary(req.keys, prefs.location, weather, prefs.days, prefs.language)
            return {"type": "itinerary", "itinerary": itinerary.dict(), "weather": weather,
                    "message": f"Listo. Te propongo un itinerario para {prefs.location} con base en el clima y lugares destacados."}

        if wants_plan:
            return {"type": "need_prefs", "message": "¿Para qué ciudad y cuántos días?"}
        if wants_weather:
            return {"type": "need_city", "message": "¿De qué ciudad necesitas el clima?"}

        # Chat general con Groq
        if Groq is None:
            return {"type": "chat", "message": "Listo para ayudarte a planear tu viaje. Dime destino y días."}
        client = Groq(api_key=req.keys.groq_api_key)
        messages = [{"role": "system", "content": "Asistente de viajes breve y claro."}]
        messages += [{"role": m.role, "content": m.content} for m in req.history]
        messages += [{"role": "user", "content": text}]

        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=messages,
            temperature=0.5,
            max_tokens=600,
        )
        answer = completion.choices[0].message.content if completion.choices else "¿A dónde te gustaría viajar?"
        return {"type": "chat", "message": answer}

# ====== RUTAS ======
@app.get("/health")
def health():
    return {"ok": True, "time": datetime.utcnow().isoformat()}

@app.post("/chat")
async def chat_endpoint(payload: ChatRequest):
    if not payload.keys or not payload.keys.groq_api_key:
        raise HTTPException(status_code=401, detail="Falta tu GROQ_API_KEY.")
    return await Orchestrator.handle(payload)

@app.get("/weather")
async def weather_endpoint(city: str, days: int = 3, start_date: Optional[str] = None):
    return await WeatherAgent.get(city, start_date, max(1, min(14, days)))

@app.post("/itinerary")
async def itinerary_endpoint(body: Dict):
    try:
        keys = UserKeys(groq_api_key=body.get("groq_api_key"), serpapi_api_key=body.get("serpapi_api_key"))
        city = body["city"]
        days = int(body.get("days", 3))
        language = body.get("language", "es")
        start_date = body.get("start_date")
    except Exception:
        raise HTTPException(status_code=400, detail="Parámetros inválidos")

    w = await WeatherAgent.get(city, start_date, max(1, min(14, days)))
    it = await PlannerAgent.plan_itinerary(keys, city, w, days, language)
    return it.dict()

@app.post("/download/txt")
async def download_txt(body: Dict):
    try:
        data = Itinerary(**body)
    except Exception:
        raise HTTPException(status_code=400, detail="Itinerario inválido")
    lines = [f"Itinerario: {data.location}", f"{data.weather_overview}", ""]
    for d in data.days:
        lines += [f"Fecha: {d.date} — {d.title}",
                  f"Mañana: {d.morning}",
                  f"Tarde: {d.afternoon}",
                  f"Noche: {d.evening}"]
        if d.notes: lines.append(f"Notas: {d.notes}")
        lines.append("")
    return {"filename": f"itinerario_{data.location.replace(' ', '_')}.txt", "content": "\n".join(lines)}

@app.post("/download/ics")
async def download_ics(body: Dict):
    try:
        data = Itinerary(**body)
    except Exception:
        raise HTTPException(status_code=400, detail="Itinerario inválido")

    def esc(s: str) -> str:
        return s.replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")

    lines = ["BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//TravelAgent//Vercel//ES"]
    for d in data.days:
        dt = datetime.fromisoformat(d.date)
        lines += [
            "BEGIN:VEVENT",
            f"DTSTART:{dt.strftime('%Y%m%d')}T090000",
            f"DTEND:{dt.strftime('%Y%m%d')}T210000",
            f"SUMMARY:{esc(f'{data.location}: {d.title}')}",
            f"DESCRIPTION:{esc(f'Mañana: {d.morning}\\nTarde: {d.afternoon}\\nNoche: {d.evening}\\n{data.weather_overview}')}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return {"filename": f"itinerario_{data.location.replace(' ', '_')}.ics", "content": "\n".join(lines)}
# NOTA: en producción, usa variables de entorno para las API keys y no las envíes desde el frontend.