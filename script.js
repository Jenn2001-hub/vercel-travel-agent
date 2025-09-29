const $ = (sel) => document.querySelector(sel);

// Keys en sessionStorage
const keysModal = $('#keysModal');
const groqInput = $('#groqKey');
const serpInput = $('#serpKey');
const getGroq = () => sessionStorage.getItem('GROQ_API_KEY') || '';
const setGroq = (v) => sessionStorage.setItem('GROQ_API_KEY', v);
const getSerp = () => sessionStorage.getItem('SERPAPI_API_KEY') || '';
const setSerp = (v) => sessionStorage.setItem('SERPAPI_API_KEY', v);

$('#saveKeys').onclick = () => {
  const g = groqInput.value.trim();
  if(!g){ alert('Ingresa tu GROQ_API_KEY'); return; }
  setGroq(g);
  setSerp(serpInput.value.trim());
  keysModal.classList.remove('visible');
};
$('#openKeys').onclick = () => { keysModal.classList.add('visible'); };

// --- Chat ---
const chatBox = $('#chatBox');
const chatForm = $('#chatForm');
const chatInput = $('#chatInput');
let history = [];

function addMsg(role, text){
  const el = document.createElement('div');
  el.className = 'msg ' + role;
  el.textContent = text;
  chatBox.appendChild(el);
  chatBox.scrollTop = chatBox.scrollHeight;
}

chatForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const text = chatInput.value.trim();
  if(!text) return;
  addMsg('user', text);
  chatInput.value='';

  const payload = {
    keys: { groq_api_key: getGroq(), serpapi_api_key: getSerp() || null },
    message: text,
    history,
  };

  // ✅ corregido
  const res = await fetch('/api/chat', { 
    method:'POST', 
    headers:{'Content-Type':'application/json'}, 
    body: JSON.stringify(payload) 
  });

  const data = await res.json().catch(()=>({message:'Error de red'}));

  if(data.type === 'need_prefs'){
    addMsg('assistant', data.message + ' (usa el formulario de itinerario a la izquierda)');
    return;
  }
  if(data.type === 'need_city'){
    addMsg('assistant', data.message + ' (escribe el nombre de la ciudad)');
    return;
  }
  if(data.type === 'itinerary'){
    addMsg('assistant', data.message);
    renderItinerary(data.itinerary);
    return;
  }
  addMsg('assistant', data.message || 'Listo.');
  history.push({ role: 'user', content: text });
  history.push({ role: 'assistant', content: data.message || '' });
});

// --- Planner ---
const planForm = $('#planForm');
const planOutput = $('#planOutput');
const downloadBtns = $('#downloadBtns');
let lastItinerary = null;

function renderItinerary(it){
  lastItinerary = it;
  let html = `<h3>Itinerario — ${it.location}</h3><p class="muted">${it.weather_overview}</p>`;
  html += '<ol class="days">';
  for(const d of it.days){
    html += `<li><h4>${d.date} · ${d.title}</h4>
      <p><b>Mañana:</b> ${d.morning}</p>
      <p><b>Tarde:</b> ${d.afternoon}</p>
      <p><b>Noche:</b> ${d.evening}</p>
      ${d.notes ? `<p><i>${d.notes}</i></p>`: ''}
    </li>`;
  }
  html += '</ol>';
  planOutput.innerHTML = html;
  planOutput.classList.remove('hidden');
  downloadBtns.classList.remove('hidden');
}

planForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const form = new FormData(planForm);
  const city = form.get('city');
  const days = parseInt(form.get('days'), 10);
  const start_date = form.get('start_date');
  const language = form.get('language') || 'es';

  const body = {
    groq_api_key: getGroq(),
    serpapi_api_key: getSerp() || null,
    city, days, start_date, language
  };

  // ✅ corregido
  const res = await fetch('/api/itinerary', { 
    method:'POST', 
    headers:{'Content-Type':'application/json'}, 
    body: JSON.stringify(body) 
  });

  if(!res.ok){
    const err = await res.json().catch(()=>({detail:'Error desconocido'}));
    alert('Error: ' + (err.detail || res.status));
    return;
  }
  const it = await res.json();
  renderItinerary(it);
  addMsg('assistant', `Generé tu itinerario para ${city}. ¡Revisa la tarjeta!`);
});

// --- Descargas ---
async function triggerDownload(endpoint, filenameFallback){
  if(!lastItinerary){ alert('Primero genera un itinerario.'); return; }
  const res = await fetch(endpoint, { 
    method:'POST', 
    headers:{'Content-Type':'application/json'}, 
    body: JSON.stringify(lastItinerary) 
  });
  const data = await res.json();
  const blob = new Blob([data.content], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = data.filename || filenameFallback;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// ✅ corregido
$('#dlTxt').onclick = () => triggerDownload('/api/download/txt', 'itinerario.txt');
$('#dlIcs').onclick = () => triggerDownload('/api/download/ics', 'itinerario.ics');

// Abre modal si no hay key
window.addEventListener('load', ()=>{ if(!getGroq()) keysModal.classList.add('visible'); });
