# api/app.py
# FastAPI backend diseñado para el Runtime de Python en Vercel.
# Endpoints: chat (orquestador), clima, planificación de itinerario y descargas (.txt / .ics).

import os
import json
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, validator

# OpenAI opcional (instalado por requirements.txt)
try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore

app = FastAPI(title="Travel Agent API", version="1.0.0")

# ----- CORS -----
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # ajusta en producción si quieres limitar
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----- Modelos -----
class UserKeys(BaseModel):
    openai_api_key: str = Field(..., min_length=10)

class ChatMessage(BaseModel):
    role: str  # "user" | "assistant" | "system"
    content: str

class TripPrefs(BaseModel):
    location: str
    days: int = Field(..., ge=1, le=14)
    start_date: Optional[str] = None  # ISO YYYY-MM-DD; por defecto mañana
    language: str = Field("es", description="Idioma de la respuesta")

    @validator("start_date", pre=True)
    def _validate_start_date(cls, v):
        if v in (None, "", "today", "mañana", "tomorrow"):
            return None
        try:
            datetime.fromisoformat(v)
            return v
        except Exception:
            raise ValueError("start_date debe ser ISO YYYY-MM-DD o vacío")

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

# ----- Constantes -----
WMO_CODE_MAP = {
    0: "despejado/soleado",
    1: "mayormente despejado",
    2: "parcialmente nublado",
    3: "nublado",
    45: "niebla",
    48: "niebla escarchada",
    51: "llovizna ligera",
    53: "llovizna moderada",
    55: "llovizna densa",
    56: "llovizna helada ligera",
    57: "llovizna helada densa",
    61: "lluvia ligera",
    63: "lluvia moderada",
    65: "lluvia intensa",
    66: "lluvia helada ligera",
    67: "lluvia helada intensa",
    71: "nieve ligera",
    73: "nieve moderada",
    75: "nieve intensa",
    77: "granizo",
    80: "chubascos ligeros",
    81: "chubascos moderados",
    82: "chubascos fuertes",
    85: "chubascos de nieve ligeros",
    86: "chubascos de nieve fuertes",
    95: "tormenta",
    96: "tormenta con granizo ligera",
    99: "tormenta con granizo fuerte",
}

# ----- Utilidades -----
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
        "latitude": lat,
        "longitude": lon,
        "daily": ",".join([
            "weathercode",
            "precipitation_sum",
            "temperature_2m_max",
            "temperature_2m_min",
        ]),
        "timezone": tz or "auto",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    url = "https://api.open-meteo.com/v1/forecast"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        d = r.json()

    daily = d.get("daily", {})
    out: List[WeatherDay] = []
    times = daily.get("time", [])
    for i, day_iso in enumerate(times):
        code = int(daily["weathercode"][i])
        out.append(
            WeatherDay(
                date=day_iso,
                code=code,
                summary=WMO_CODE_MAP.get(code, ""),
                temp_max=float(daily["temperature_2m_max"][i]),
                temp_min=float(daily["temperature_2m_min"][i]),
                precipitation_sum=float(daily.get("precipitation_sum", [0.0]*len(times))[i]),
            )
        )
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
    temp_range = (min(d.temp_min for d in days), max(d.temp_max for d in days))
    return f"Panorama general: {overall}. Temperaturas entre {temp_range[0]:.0f}°C y {temp_range[1]:.0f}°C. Lluvia acumulada aprox. {total_rain:.1f} mm."

# ----- Agentes -----
class WeatherAgent:
    @staticmethod
    async def get(city: str, start: Optional[str], n_days: int) -> Dict:
        geo = await geocode_city(city)
        start_date = (
            datetime.fromisoformat(start).date() if start else (date.today() + timedelta(days=1))
        )
        days = await fetch_weather(geo["lat"], geo["lon"], geo["timezone"], start_date, n_days)
        return {"geo": geo, "days": [d.dict() for d in days], "overview": summarize_weather(days)}

class PlannerAgent:
    @staticmethod
    async def plan_itinerary(keys: UserKeys, city: str, weather: Dict, n_days: int, language: str) -> Itinerary:
        if OpenAI is None:
            raise HTTPException(status_code=500, detail="OpenAI SDK no disponible en el entorno.")
        client = OpenAI(api_key=keys.openai_api_key)

        schema = {
            "name": "TravelItinerary",
            "schema": {
                "type": "object",
                "properties": {
                    "location": {"type": "string"},
                    "days": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "date": {"type": "string"},
                                "title": {"type": "string"},
                                "morning": {"type": "string"},
                                "afternoon": {"type": "string"},
                                "evening": {"type": "string"},
                                "notes": {"type": "string"},
                            },
                            "required": ["date", "title", "morning", "afternoon", "evening"],
                        },
                    },
                    "weather_overview": {"type": "string"},
                },
                "required": ["location", "days", "weather_overview"],
                "additionalProperties": False,
            },
            "strict": True,
        }

        weather_overview = weather.get("overview", "")
        day_objs = weather.get("days", [])

        guide_lines = []
        for d in day_objs:
            hint = ""
            if d["precipitation_sum"] >= 2:
                hint = "(día lluvioso: prioriza planes bajo techo)"
            elif d["code"] in (0, 1):
                hint = "(día soleado: actividades al aire libre recomendadas)"
            guide_lines.append(f"{d['date']}: {WMO_CODE_MAP.get(d['code'], '')} {hint}")
        guide_text = "\n".join(guide_lines)

        system_prompt = (
            "Eres un agente de viajes detallista y práctico. Devuelve respuestas claras en el idioma solicitado. "
            "Cumple estrictamente el esquema JSON indicado. Limita a planes realistas, con tiempos y zonas agrupadas "
            "para minimizar traslados. Incluye comida local, transporte sugerido y alternativas si llueve. No inventes precios."
        )

        user_prompt = f"""
Genera un itinerario para {n_days} día(s) en {city}.
Idioma: {language}.
Resumen del clima: {weather_overview}.
Guía por día:
{guide_text}
Estructura exacta: devuelve sólo JSON que cumpla el esquema (sin texto adicional).
"""

        resp = client.responses.create(
            model="gpt-4.1-mini",
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_schema", "json_schema": schema},
        )

        # Extraer JSON
        try:
            content = resp.output[0].content[0].text  # SDK 1.x Responses API
            data = json.loads(content)
        except Exception:
            raw = getattr(resp, "output_text", None) or getattr(resp, "content", None)
            data = json.loads(raw) if isinstance(raw, str) else {}

        itinerary = Itinerary(**data)
        return itinerary

# ----- Orquestador -----
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
            return {
                "type": "itinerary",
                "itinerary": itinerary.dict(),
                "weather": weather,
                "message": f"Listo. Te propongo un itinerario para {prefs.location} con base en el clima previsto.",
            }

        if wants_plan:
            return {"type": "need_prefs", "message": "¿Para qué ciudad y cuántos días?"}

        if wants_weather:
            return {"type": "need_city", "message": "¿De qué ciudad necesitas el clima?"}

        if OpenAI is None:
            return {"type": "chat", "message": "Estoy listo para ayudarte a planear tu viaje. Dime destino y días."}
        client = OpenAI(api_key=req.keys.openai_api_key)
        sys = "Eres un asistente de viajes amable y útil. Responde de forma breve y clara."
        resp = client.responses.create(
            model="gpt-4.1-mini",
            input=[
                {"role": "system", "content": sys},
                *[{"role": m.role, "content": m.content} for m in req.history],
                {"role": "user", "content": text},
            ],
        )
        try:
            answer = resp.output_text
        except Exception:
            answer = "Puedo ayudarte con destinos, clima e itinerarios. ¿A dónde te gustaría viajar?"
        return {"type": "chat", "message": answer}

# ----- Rutas -----
@app.get("/health")
def health():
    return {"ok": True, "time": datetime.utcnow().isoformat()}

@app.post("/chat")
async def chat_endpoint(payload: ChatRequest):
    if not payload.keys or not payload.keys.openai_api_key:
        raise HTTPException(status_code=401, detail="Falta tu OPENAI_API_KEY.")
    result = await Orchestrator.handle(payload)
    return result

@app.get("/weather")
async def weather_endpoint(city: str, days: int = 3, start_date: Optional[str] = None):
    w = await WeatherAgent.get(city, start_date, max(1, min(14, days)))
    return w

@app.post("/itinerary")
async def itinerary_endpoint(body: Dict):
    try:
        keys = UserKeys(openai_api_key=body.get("openai_api_key"))
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
        lines.append(f"Fecha: {d.date} — {d.title}")
        lines.append(f"Mañana: {d.morning}")
        lines.append(f"Tarde: {d.afternoon}")
        lines.append(f"Noche: {d.evening}")
        if d.notes:
            lines.append(f"Notas: {d.notes}")
        lines.append("")
    return {"filename": f"itinerario_{data.location.replace(' ', '_')}.txt", "content": "\n".join(lines)}

@app.post("/download/ics")
async def download_ics(body: Dict):
    try:
        data = Itinerary(**body)
    except Exception:
        raise HTTPException(status_code=400, detail="Itinerario inválido")

    def ics_escape(s: str) -> str:
        return s.replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//TravelAgent//Vercel//ES",
    ]
    for d in data.days:
        dt = datetime.fromisoformat(d.date)
        dtstart = dt.strftime("%Y%m%d") + "T090000"
        dtend = dt.strftime("%Y%m%d") + "T210000"
        summary = ics_escape(f"{data.location}: {d.title}")
        desc = ics_escape(
            f"Mañana: {d.morning}\nTarde: {d.afternoon}\nNoche: {d.evening}\n{data.weather_overview}"
        )
        lines += [
            "BEGIN:VEVENT",
            f"DTSTART:{dtstart}",
            f"DTEND:{dtend}",
            f"SUMMARY:{summary}",
            f"DESCRIPTION:{desc}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return {"filename": f"itinerario_{data.location.replace(' ', '_')}.ics", "content": "\n".join(lines)}
