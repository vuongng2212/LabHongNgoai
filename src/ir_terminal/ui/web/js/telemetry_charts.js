(function () {
  'use strict';

  function drawSingleChart(canvas, data, color, unit, maxVal) {
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    if (canvas.width !== rect.width * dpr || canvas.height !== rect.height * dpr) {
      canvas.width = rect.width * dpr;
      canvas.height = rect.height * dpr;
    }
    ctx.resetTransform();
    ctx.scale(dpr, dpr);

    const w = rect.width;
    const h = rect.height;
    ctx.clearRect(0, 0, w, h);

    ctx.strokeStyle = '#D3E4FE';
    ctx.lineWidth = 0.8;
    ctx.setLineDash([3, 3]);
    for (let y = 0.25; y <= 0.75; y += 0.25) {
      ctx.beginPath();
      ctx.moveTo(0, h * y);
      ctx.lineTo(w, h * y);
      ctx.stroke();
    }
    ctx.setLineDash([]);

    if (data.length < 2) return;
    const step = w / (data.length - 1);

    ctx.beginPath();
    const pts = data.map((val, idx) => {
      const clamped = Math.min(Math.max(val, 0), maxVal);
      const y = h - (clamped / maxVal) * (h - 20) - 10;
      const x = idx * step;
      return { x, y };
    });

    ctx.moveTo(pts[0].x, pts[0].y);
    for (let i = 1; i < pts.length; i++) {
      const xc = (pts[i].x + pts[i - 1].x) / 2;
      const yc = (pts[i].y + pts[i - 1].y) / 2;
      ctx.quadraticCurveTo(pts[i - 1].x, pts[i - 1].y, xc, yc);
    }
    ctx.lineTo(pts[pts.length - 1].x, pts[pts.length - 1].y);
    ctx.strokeStyle = color;
    ctx.lineWidth = 2.5;
    ctx.lineCap = 'round';
    ctx.stroke();

    ctx.lineTo(w, h);
    ctx.lineTo(0, h);
    ctx.closePath();
    const grad = ctx.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, color + '33');
    grad.addColorStop(1, color + '00');
    ctx.fillStyle = grad;
    ctx.fill();

    const last = pts[pts.length - 1];
    ctx.beginPath();
    ctx.arc(last.x, last.y, 4, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.strokeStyle = '#FFFFFF';
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }

  function drawCharts(speedCanvas, rttCanvas, speedHistory, rttHistory) {
    drawSingleChart(speedCanvas, speedHistory, '#2563EB', 'KB/s', 12.0);
    drawSingleChart(rttCanvas, rttHistory, '#006C49', 'ms', 100.0);
  }

  window.SpectraLinkCharts = {
    drawSingleChart: drawSingleChart,
    drawCharts: drawCharts,
  };
})();
