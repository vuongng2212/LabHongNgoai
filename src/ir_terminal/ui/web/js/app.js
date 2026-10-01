// Main Application Router & Controller
document.addEventListener('DOMContentLoaded', () => {
  const navItems = document.querySelectorAll('.nav-item');
  const viewPanels = document.querySelectorAll('.view-panel');
  const centerTitle = document.getElementById('window-center-title');

  const titleMap = {
    'view-hardware': 'SpectraLink IR-OWC — Hardware & Optical Link Setup',
    'view-file': 'SpectraLink IR-OWC — File & Media Transfer Station',
    'view-chat': 'SpectraLink IR-OWC — Realtime Optical Chat Stream',
    'view-telemetry': 'SpectraLink IR-OWC — Lab Diagnostics & Telemetry'
  };

  window.switchView = function(viewId) {
    navItems.forEach(item => {
      const target = item.getAttribute('data-target');
      if (target === viewId) {
        item.className = 'nav-item flex items-center gap-space-sm px-space-md py-space-xs rounded-lg bg-primary-container text-white font-medium shadow-sm transition-all cursor-pointer';
      } else {
        item.className = 'nav-item flex items-center gap-space-sm px-space-md py-space-xs rounded-lg text-on-surface-variant hover:text-on-surface hover:bg-surface-container-highest/60 transition-colors cursor-pointer';
      }
    });

    viewPanels.forEach(panel => {
      if (panel.id === viewId) {
        panel.classList.add('active');
      } else {
        panel.classList.remove('active');
      }
    });

    if (centerTitle) {
      centerTitle.innerText = titleMap[viewId] || 'SpectraLink IR-OWC';
    }
  };

  navItems.forEach(item => {
    item.addEventListener('click', () => {
      const target = item.getAttribute('data-target');
      window.switchView(target);
    });
  });
});
