// Chat Viewport Controller with persistent database history loading
function initChatController() {
  const chatInput = document.querySelector('#view-chat input[type="text"]');
  const sendBtn = document.querySelector('#view-chat button.bg-primary-container');
  const chatContainer = document.querySelector('#view-chat .overflow-y-auto');
  const hexDumpElem = document.querySelector('#view-chat pre');

  if (!chatInput || !sendBtn) return;

  function doSend() {
    const text = chatInput.value.trim();
    if (!text || !window.bridge) return;
    window.bridge.sendChat(text);
    chatInput.value = '';
  }

  sendBtn.addEventListener('click', doSend);
  chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      doSend();
    }
  });

  if (window.bridge) {
    window.bridge.chatMessageReceived.connect((text, timeStr, isOutgoing) => {
      appendChatMessage(text, timeStr, isOutgoing);
    });

    window.bridge.chatFrameInspected.connect((hexDump, seq, opcode, len) => {
      if (hexDumpElem) {
        hexDumpElem.innerText = `[FRAME SEQ #${seq} | CMD 0x${opcode.toString(16).padStart(2, '0').toUpperCase()} | LEN ${len}B]\n` + hexDump;
      }
    });

    // Load persisted chat history from local hard drive SQLite database
    if (typeof window.bridge.getChatHistory === 'function') {
      window.bridge.getChatHistory((historyJson) => {
        try {
          const messages = JSON.parse(historyJson);
          if (Array.isArray(messages) && messages.length > 0 && chatContainer) {
            chatContainer.innerHTML = '';
            messages.forEach(msg => {
              appendChatMessage(msg.text, msg.timestamp, msg.is_outgoing);
            });
          }
        } catch (err) {
          console.error('Failed to parse chat history JSON:', err);
        }
      });
    }
  }

  function appendChatMessage(text, timeStr, isOutgoing) {
    const wrapper = document.createElement('div');
    wrapper.className = isOutgoing ? 'flex justify-end' : 'flex justify-start';

    if (isOutgoing) {
      wrapper.innerHTML = `
        <div class="max-w-[75%] rounded-2xl px-4 py-2.5 bg-primary-container text-white shadow-sm space-y-1">
          <div class="text-[13px] leading-relaxed font-body-md">${escapeHtml(text)}</div>
          <div class="flex items-center justify-end gap-1 text-[10px] text-blue-100 font-mono-caption">
            <span>${timeStr}</span>
            <span class="material-symbols-outlined text-[13px]">done_all</span>
          </div>
        </div>
      `;
    } else {
      wrapper.innerHTML = `
        <div class="max-w-[75%] rounded-2xl px-4 py-2.5 bg-white text-on-surface border border-black/[0.06] shadow-sm space-y-1">
          <div class="text-[11px] font-semibold text-secondary font-mono-caption">Station Bravo (Remote 880nm)</div>
          <div class="text-[13px] leading-relaxed font-body-md">${escapeHtml(text)}</div>
          <div class="flex items-center justify-end gap-1 text-[10px] text-outline font-mono-caption">
            <span>${timeStr}</span>
          </div>
        </div>
      `;
    }

    if (chatContainer) {
      chatContainer.appendChild(wrapper);
      chatContainer.scrollTop = chatContainer.scrollHeight;
    }
  }

  function escapeHtml(str) {
    return str.replace(/[&<>'"]/g, tag => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      "'": '&#39;',
      '"': '&quot;'
    }[tag] || tag));
  }
}

document.addEventListener('DOMContentLoaded', () => {
  setTimeout(initChatController, 200);
});
