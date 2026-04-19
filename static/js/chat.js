let history = [];

async function sendMessage() {
    const input = document.getElementById("user-input");
    const text = input.value.trim();
    if (!text) return;

    addMessage(text, "user");
    input.value = "";

    try {
        const response = await fetch("/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: text, history: history })
        });
        const data = await response.json();
        addMessage(data.reply, "bot");
        history.push({ role: "user", content: text });
        history.push({ role: "assistant", content: data.reply });
    } catch (error) {
        addMessage("❌ Error conectando con el servidor.", "bot");
    }
}

function addMessage(text, sender) {
    const div = document.createElement("div");
    div.className = "msg " + sender;

    if (sender === "bot") {
        div.innerHTML = marked.parse(text);
    } else {
        div.textContent = text;
    }

    const messages = document.getElementById("messages");
    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
}