const toggle = document.getElementById('chat-toggle');
const chatWindow = document.getElementById('chat-window');
const closeBtn = document.getElementById('chat-close');
const sendBtn = document.getElementById('chat-send');
const input = document.getElementById('chat-input');
const messages = document.getElementById('chat-messages');
const attachBtn = document.getElementById('chat-attach');
const fileInput = document.getElementById('chat-file-input');

let history = [];
let isOpen = false;

// ID único por sesión de navegador
const SESSION_ID = crypto.randomUUID();

toggle.addEventListener('click', () => {
  isOpen = !isOpen;
  chatWindow.classList.toggle('open', isOpen);
  if (isOpen) input.focus();
});

closeBtn.addEventListener('click', () => {
  isOpen = false;
  chatWindow.classList.remove('open');
});

// Enter envía; Shift+Enter inserta un salto de línea
input.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

// Auto-crecimiento vertical del textarea a medida que se escribe
function autoResize() {
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 120) + 'px';
  input.style.overflowY = input.scrollHeight > 120 ? 'auto' : 'hidden';
}
input.addEventListener('input', autoResize);

sendBtn.addEventListener('click', sendMessage);

function addMessage(text, sender) {
  const div = document.createElement('div');
  div.className = 'msg ' + sender;
  if (sender === 'bot') {
    div.innerHTML = marked.parse(text);
  } else {
    div.textContent = text;
  }
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
}

function showTyping() {
  const div = document.createElement('div');
  div.className = 'typing-indicator';
  div.id = 'typing';
  div.innerHTML = '<span></span><span></span><span></span>';
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
}

function hideTyping() {
  const t = document.getElementById('typing');
  if (t) t.remove();
}

async function sendMessage() {
  const text = input.value.trim();
  if (!text) return;

  addMessage(text, 'user');
  input.value = '';
  input.style.height = 'auto';
  input.style.overflowY = 'hidden';
  showTyping();

  try {
    const res = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, history, session_id: SESSION_ID })
    });
    const data = await res.json();
    hideTyping();
    addMessage(data.reply, 'bot');
    history.push({ role: 'user', content: text });
    history.push({ role: 'assistant', content: data.reply });
  } catch (err) {
    hideTyping();
    addMessage('❌ Error al conectar con el servidor.', 'bot');
  }
}

attachBtn.addEventListener('click', () => fileInput.click());

fileInput.addEventListener('change', async () => {
  const file = fileInput.files[0];
  fileInput.value = ''; // permite volver a subir el mismo archivo si se repite
  if (!file) return;

  addMessage(`📎 ${file.name}`, 'user');
  showTyping();

  try {
    const formData = new FormData();
    formData.append('archivo', file);
    formData.append('session_id', SESSION_ID);

    const res = await fetch('/chat/documento', { method: 'POST', body: formData });
    const data = await res.json();
    hideTyping();
    addMessage(data.reply, 'bot');
    history.push({ role: 'user', content: `📎 ${file.name}` });
    history.push({ role: 'assistant', content: data.reply });
  } catch (err) {
    hideTyping();
    addMessage('❌ Error al subir el documento.', 'bot');
  }
});