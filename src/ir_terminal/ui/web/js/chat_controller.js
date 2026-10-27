function initChatController() {
  const chatInput = document.querySelector('#view-chat input');
  const sendBtn = document.querySelector('#view-chat button.bg-primary-container, #view-chat button:has(span[data-icon="send"])');
  const chatContainer = document.querySelector('#view-chat section.overflow-y-auto');
  const hexDumpElem = document.querySelector('#view-chat pre');

  function doSend() {
    if (!chatInput) return;
    const text = chatInput.value.trim();
    if (!text) {
      if (window.UIFeedback) window.UIFeedback.showToast('warning', 'Empty message', 'Type a payload string before sending.');
      chatInput.focus();
      return;
    }
    if (new Blob([text]).size > 512) {
      if (window.UIFeedback) window.UIFeedback.showToast('warning', 'Payload too long', 'Maximum packet length is 512 bytes.');
      return;
    }
    if (!window.bridge) {
      if (window.UIFeedback) window.UIFeedback.showToast('error', 'Backend not ready', 'Bridge is still connecting — try again in a moment.');
      return;
    }
    window.bridge.sendChat(text);
    chatInput.value = '';
  }

  if (sendBtn) {
    sendBtn.addEventListener('click', (e) => {
      e.preventDefault();
      doSend();
    });
  }

  if (chatInput) {
    chatInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        doSend();
      }
    });
  }

  function renderEmptyChatState() {
    if (!chatContainer) return;
    chatContainer.innerHTML = `
      <div id="chat-empty-state" class="flex flex-col items-center justify-center py-16 text-center text-on-surface-variant my-auto">
        <div class="w-12 h-12 rounded-2xl bg-surface-container flex items-center justify-center text-primary mb-3 shadow-xs">
          <span class="material-symbols-outlined text-[24px]">forum</span>
        </div>
        <p class="text-title-sm font-title-sm font-semibold text-on-surface mb-1">No optical messages yet</p>
        <p class="text-body-md font-body-md text-outline max-w-sm">Type a text message below and press Enter to transmit across the 880nm Line-Of-Sight optical carrier.</p>
      </div>
    `;
  }

  function tryWireBridge() {
    if (window.SpectraLinkBridge) window.SpectraLinkBridge.onReady('chat', wireBridgeSignals);
    else if (!window.bridge) setTimeout(tryWireBridge, 300);
    else wireBridgeSignals();
  }

  function wireBridgeSignals() {
    window.bridge.chatMessageReceived.connect((text, timeStr, isOutgoing) => {
      appendChatMessage(text, timeStr, isOutgoing);
    });

    window.bridge.chatFrameInspected.connect((hexDump, seq, opcode, len) => {
      if (hexDumpElem) {
        hexDumpElem.innerText = `[FRAME SEQ #${seq} | CMD 0x${opcode.toString(16).padStart(2, '0').toUpperCase()} | LEN ${len}B]\n` + hexDump;
      }
    });

    if (typeof window.bridge.getChatHistory === 'function') {
      window.bridge.getChatHistory((historyJson) => {
        try {
          const messages = JSON.parse(historyJson);
          if (Array.isArray(messages) && messages.length > 0 && chatContainer) {
            chatContainer.innerHTML = '';
            const fragment = document.createDocumentFragment();
            messages.forEach(msg => {
              appendChatMessage(msg.text, msg.timestamp, msg.is_outgoing, fragment);
            });
            chatContainer.appendChild(fragment);
            chatContainer.scrollTop = chatContainer.scrollHeight;
          } else {
            renderEmptyChatState();
          }
        } catch (err) {
          console.error('Failed to parse chat history JSON:', err);
          renderEmptyChatState();
        }
      });
    } else {
      renderEmptyChatState();
    }
  }

  tryWireBridge();

  function appendChatMessage(text, timeStr, isOutgoing, target) {
    const container = target || chatContainer;
    if (!container) return;
    const emptyState = document.getElementById('chat-empty-state');
    if (emptyState) {
      emptyState.remove();
    }

    const wrapper = document.createElement('div');
    if (isOutgoing) {
      wrapper.className = 'flex flex-col items-end ml-auto max-w-xl group';
      wrapper.innerHTML = `
        <div class="flex items-center gap-2 mb-1 px-1">
          <span class="text-mono-caption font-mono-caption text-outline">${escapeHtml(timeStr)}</span>
          <span class="text-label-sm font-label-sm font-bold text-on-surface">Local Host (TX-880nm)</span>
          <span class="w-1.5 h-1.5 rounded-full bg-primary-container"></span>
        </div>
        <div class="px-4 py-2.5 rounded-2xl rounded-tr-sm bg-primary-container text-white text-body-md font-body-md shadow-sm leading-relaxed">
          ${escapeHtml(text)}
        </div>
        <div class="flex items-center gap-1.5 mt-1.5 px-1 text-mono-caption font-mono-caption text-primary">
          <span class="material-symbols-outlined text-[13px]">done_all</span>
          <span class="font-medium">ACK Confirmed</span>
        </div>
      `;
    } else {
      wrapper.className = 'flex flex-col items-start max-w-xl group';
      wrapper.innerHTML = `
        <div class="flex items-center gap-2 mb-1 px-1">
          <span class="w-1.5 h-1.5 rounded-full bg-secondary"></span>
          <span class="text-label-sm font-label-sm font-bold text-on-surface">Remote Node (Station Bravo • 880nm)</span>
          <span class="text-mono-caption font-mono-caption text-outline">${escapeHtml(timeStr)}</span>
        </div>
        <div class="px-4 py-2.5 rounded-2xl rounded-tl-sm bg-[#EAECEF] border border-outline-variant/50 text-[#1F2937] text-body-md font-body-md shadow-xs leading-relaxed">
          ${escapeHtml(text)}
        </div>
        <div class="flex items-center gap-2 mt-1.5 px-1 text-mono-caption font-mono-caption text-outline">
          <span class="px-1.5 py-0.5 rounded bg-surface-container border border-outline-variant/40 font-mono">880nm NIR</span>
          <span>•</span>
          <span class="text-secondary font-medium">CRC32 Valid</span>
          <span>•</span>
          <span>0 Dropped</span>
        </div>
      `;
    }

    container.appendChild(wrapper);
    if (!target) {
      chatContainer.scrollTop = chatContainer.scrollHeight;
    }
  }

  function escapeHtml(str) {
    if (window.SpectraLinkUtils) return window.SpectraLinkUtils.escapeHtml(str);
    if (!str) return '';
    return String(str);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  setTimeout(initChatController, 100);
});
