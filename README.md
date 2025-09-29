# Travel Agent – FastAPI + HTML/CSS (Vercel)

Este proyecto es un **Agente de Viajes interactivo**:

- El usuario ingresa su **GROQ_API_KEY** (y opcionalmente una **SERPAPI_API_KEY**).
- Puede **chatear** con el agente para pedir recomendaciones de viaje.
- Puede **generar itinerarios** completos por ciudad y días.
- Integra **clima** (Open-Meteo) y lugares recomendados (SerpAPI).
- Permite **descargar** el itinerario en `.txt` y calendario `.ics`.

Backend en **FastAPI (Python)** y frontend **HTML + CSS + JS**. Se despliega en **Vercel**.

## Estructura del proyecto
vercel-travel-agent/
│
├─ api/
│ └─ index.py # Backend FastAPI (todas las rutas de la API)
│
├─ index.html # Frontend principal (formulario + chat)
├─ styles.css # Estilos del frontend
├─ script.js # Lógica del chat y del planificador
│
├─ requirements.txt # Dependencias de Python
└─ README.md # Este archivo


## Dependencias (requirements.txt)

fastapi==0.115.0
uvicorn==0.30.6
httpx==0.27.2
pydantic==2.8.2
groq==0.9.0


## Ejecutar localmente

**Requisitos**: Python 3.10+ (recomendado 3.11/3.12)

1) Instala dependencias:
pip install -r requirements.txt

Arranca el backend:
uvicorn api.index:app --reload --port 8000

Endpoints locales:
http://localhost:8000/health
http://localhost:8000/chat
http://localhost:8000/itinerary
http://localhost:8000/download/txt
http://localhost:8000/download/ics

Sirve el frontend (en otra terminal):
python -m http.server 8080
Abre http://localhost:8080 y usa la app.

Despliegue en Vercel
Sube este repo a GitHub/GitLab/Bitbucket.
Entra a https://vercel.com → Add New Project → Importa el repo.
En Project Settings → Build & Development configura:
Framework Preset: Other
Build Command: (vacío)
Output Directory: (vacío)
Root Directory: / (raíz del repo)
Deploy. Si modificas algo, usa Redeploy → Clear Build Cache.

Rutas públicas en producción
Como el backend está en api/index.py, Vercel expone estas rutas:
/api/health
/api/chat
/api/itinerary
/api/download/txt
/api/download/ics
/api/weather


API Keys
Al abrir la web se te pedirá tu GROQ_API_KEY (obligatoria) y SERPAPI_API_KEY (opcional).
Se guardan en sessionStorage del navegador y se envían al backend solo para procesar tu solicitud.
No se almacenan en el servidor.


Proyecto educativo para practicar:
FastAPI (Python, ASGI)
Groq para generación de texto
Open-Meteo para clima (sin API key)
SerpAPI (opcional) para enriquecer lugares
Despliegue serverless en Vercel
