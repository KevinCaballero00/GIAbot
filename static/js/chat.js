const toggle = document.getElementById('chat-toggle');
const chatWindow = document.getElementById('chat-window');
const closeBtn = document.getElementById('chat-close');
const sendBtn = document.getElementById('chat-send');
const input = document.getElementById('chat-input');
const messages = document.getElementById('chat-messages');

let history = [];
let isOpen = false;

toggle.addEventListener('click', () => {
  isOpen = !isOpen;
  chatWindow.classList.toggle('open', isOpen);
  if (isOpen) input.focus();
});

closeBtn.addEventListener('click', () => {
  isOpen = false;
  chatWindow.classList.remove('open');
});

input.addEventListener('keypress', e => {
  if (e.key === 'Enter') sendMessage();
});

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
  showTyping();

  try {
    const res = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, history })
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