const CORES_GRAFICO = [
  "#6366f1",
  "#10b981",
  "#0ea5e9",
  "#f59e0b",
  "#ec4899",
  "#8b5cf6",
  "#14b8a6",
  "#f43f5e",
];

window.__graficosRegistrados = window.__graficosRegistrados || [];

function obterCoresTema() {
  const escuro = document.documentElement.dataset.theme === "dark";
  return {
    texto: escuro ? "#cbd5e1" : "#475569",
    grade: escuro ? "rgba(148, 163, 184, 0.14)" : "rgba(15, 23, 42, 0.08)",
    bordaFatia: escuro ? "#111827" : "#ffffff",
    destaqueHover: escuro ? "#f8fafc" : "#0f172a",
    tooltipBg: escuro ? "#0f172a" : "#1e293b",
    tooltipTexto: "#f8fafc",
    faixaHover: escuro ? "rgba(129, 140, 248, 0.14)" : "rgba(99, 102, 241, 0.09)",
    primaria: escuro ? "#818cf8" : "#6366f1",
    primariaArea: escuro ? "rgba(129, 140, 248, 0.18)" : "rgba(99, 102, 241, 0.14)",
    sucesso: escuro ? "#34d399" : "#10b981",
    perigo: escuro ? "#fb7185" : "#f43f5e",
  };
}

function formatarBRL(valor) {
  const num = Number(valor) || 0;
  return num.toLocaleString("pt-BR", {
    style: "currency",
    currency: "BRL",
  });
}

function aplicarTemaEmGrafico(chart) {
  const t = obterCoresTema();
  if (chart.options.plugins?.legend?.labels) {
    chart.options.plugins.legend.labels.color = t.texto;
  }
  if (chart.options.plugins?.tooltip) {
    chart.options.plugins.tooltip.backgroundColor = t.tooltipBg;
    chart.options.plugins.tooltip.titleColor = t.tooltipTexto;
    chart.options.plugins.tooltip.bodyColor = t.tooltipTexto;
  }
  if (chart.options.scales) {
    Object.values(chart.options.scales).forEach((axis) => {
      if (axis.ticks) axis.ticks.color = t.texto;
      if (axis.grid) axis.grid.color = t.grade;
    });
  }
  if (chart.config.type === "doughnut") {
    chart.data.datasets.forEach((ds) => {
      ds.borderColor = t.bordaFatia;
      ds.hoverBorderColor = t.bordaFatia;
    });
  }
  if (chart.config.type === "line" && chart.data.datasets.length >= 3) {
    chart.data.datasets[0].borderColor = t.primaria;
    chart.data.datasets[0].backgroundColor = t.primariaArea;
    chart.data.datasets[0].pointBackgroundColor = t.primaria;
    chart.data.datasets[1].borderColor = t.sucesso;
    chart.data.datasets[1].pointBackgroundColor = t.sucesso;
    chart.data.datasets[2].borderColor = t.perigo;
    chart.data.datasets[2].pointBackgroundColor = t.perigo;
  }
  chart.update("none");
}

window.atualizarTemaGraficos = function () {
  window.__graficosRegistrados.forEach(aplicarTemaEmGrafico);
};

// Plugin de destaque visual ao passar o mouse: realça e amplia o item focado.
const zoomHoverPlugin = {
  id: "zoomHoverPlugin",
  beforeDatasetsDraw(chart) {
    const active = chart.getActiveElements();
    if (!active || !active.length) return;

    const { ctx, chartArea } = chart;
    if (chart.config.type === "line") {
      const t = obterCoresTema();
      const x = active[0].element.x;
      ctx.save();
      ctx.fillStyle = t.faixaHover;
      ctx.fillRect(x - 18, chartArea.top, 36, chartArea.bottom - chartArea.top);
      ctx.restore();
    }
  },
  afterDatasetsDraw(chart) {
    const active = chart.getActiveElements();
    if (!active || !active.length || chart.config.type !== "bar") return;

    const t = obterCoresTema();
    const { ctx } = chart;
    const item = active[0];
    const meta = chart.getDatasetMeta(item.datasetIndex);
    const bar = meta.data[item.index];
    if (!bar) return;

    ctx.save();
    ctx.strokeStyle = t.destaqueHover;
    ctx.lineWidth = 2.5;
    const pad = 3;
    const props = bar.getProps(["x", "y", "base", "width", "height"], true);
    if (chart.options.indexAxis === "y") {
      const left = Math.min(props.base, props.x);
      const w = Math.abs(props.x - props.base);
      ctx.strokeRect(left - pad, props.y - props.height / 2 - pad, w + pad * 2, props.height + pad * 2);
    } else {
      const top = Math.min(props.base, props.y);
      const h = Math.abs(props.base - props.y);
      ctx.strokeRect(props.x - props.width / 2 - pad, top - pad, props.width + pad * 2, h + pad * 2);
    }
    ctx.restore();
  },
};

function criarGraficoEvolucao(canvasId, serieCompleta, leituraId) {
  const canvas = document.getElementById(canvasId);
  if (!canvas || typeof Chart === "undefined") return null;

  const t = obterCoresTema();
  const leitura = leituraId ? document.getElementById(leituraId) : null;
  let dadosAtuais = serieCompleta.slice(-6);

  const construirDados = (serie) => {
    const tema = obterCoresTema();
    return {
      labels: serie.map((p) => p.label),
      datasets: [
        {
          label: "Patrimônio líquido",
          data: serie.map((p) => p.patrimonio),
          borderColor: tema.primaria,
          backgroundColor: tema.primariaArea,
          fill: true,
          tension: 0.34,
          borderWidth: 3,
          pointRadius: 5,
          pointHoverRadius: 13,
          pointHoverBorderWidth: 3,
          pointBackgroundColor: tema.primaria,
          pointHoverBackgroundColor: "#ffffff",
        },
        {
          label: "Total guardado",
          data: serie.map((p) => p.guardado),
          borderColor: tema.sucesso,
          backgroundColor: "transparent",
          borderDash: [5, 4],
          tension: 0.34,
          borderWidth: 2,
          pointRadius: 4,
          pointHoverRadius: 11,
          pointHoverBorderWidth: 3,
          pointBackgroundColor: tema.sucesso,
          pointHoverBackgroundColor: "#ffffff",
        },
        {
          label: "Dívida no cartão",
          data: serie.map((p) => p.divida),
          borderColor: tema.perigo,
          backgroundColor: "transparent",
          tension: 0.34,
          borderWidth: 2,
          pointRadius: 4,
          pointHoverRadius: 11,
          pointHoverBorderWidth: 3,
          pointBackgroundColor: tema.perigo,
          pointHoverBackgroundColor: "#ffffff",
        },
      ],
    };
  };

  const chart = new Chart(canvas, {
    type: "line",
    data: construirDados(dadosAtuais),
    plugins: [zoomHoverPlugin],
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      animation: { duration: 240 },
      onHover(event, elements) {
        canvas.style.cursor = elements.length ? "pointer" : "default";
        if (!leitura) return;
        if (elements.length) {
          const p = dadosAtuais[elements[0].index];
          if (p) {
            leitura.textContent =
              `${p.label} — Patrimônio: ${formatarBRL(p.patrimonio)} | ` +
              `Guardado: ${formatarBRL(p.guardado)} | Dívida: ${formatarBRL(p.divida)}`;
          }
        } else {
          leitura.textContent = "Passe o mouse sobre os meses para dar zoom nos detalhes.";
        }
      },
      plugins: {
        legend: { position: "bottom", labels: { color: t.texto } },
        tooltip: {
          backgroundColor: t.tooltipBg,
          titleColor: t.tooltipTexto,
          bodyColor: t.tooltipTexto,
          padding: 12,
          titleFont: { size: 14 },
          bodyFont: { size: 13 },
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: ${formatarBRL(ctx.parsed.y)}`,
          },
        },
      },
      scales: {
        x: {
          ticks: { color: t.texto },
          grid: { color: t.grade },
        },
        y: {
          ticks: {
            color: t.texto,
            callback: (v) => formatarBRL(v),
          },
          grid: { color: t.grade },
        },
      },
    },
  });

  window.__graficosRegistrados.push(chart);

  document.querySelectorAll("[data-periodo]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("[data-periodo]").forEach((b) => b.classList.remove("ativo"));
      btn.classList.add("ativo");
      const qtd = Number(btn.dataset.periodo);
      dadosAtuais = qtd > 0 ? serieCompleta.slice(-qtd) : serieCompleta.slice();
      chart.data = construirDados(dadosAtuais);
      chart.update();
    });
  });

  return chart;
}

function criarGraficoRoscaZoom(canvasId, labels, valores, leituraId) {
  const canvas = document.getElementById(canvasId);
  if (!canvas || typeof Chart === "undefined" || !labels.length) return null;

  const t = obterCoresTema();
  const leitura = leituraId ? document.getElementById(leituraId) : null;

  const estado = {
    labels: labels.slice(),
    valores: valores.slice(),
    total: valores.reduce((acc, v) => acc + v, 0),
  };

  const chart = new Chart(canvas, {
    type: "doughnut",
    data: {
      labels: estado.labels,
      datasets: [
        {
          data: estado.valores,
          backgroundColor: estado.labels.map((_, i) => CORES_GRAFICO[i % CORES_GRAFICO.length]),
          borderColor: t.bordaFatia,
          borderWidth: 2,
          hoverOffset: 28,
          hoverBorderWidth: 4,
          hoverBorderColor: t.bordaFatia,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: "58%",
      layout: { padding: 24 },
      animation: { duration: 220 },
      onHover(event, elements) {
        canvas.style.cursor = elements.length ? "pointer" : "default";
        if (!leitura) return;
        if (elements.length) {
          const i = elements[0].index;
          const val = estado.valores[i];
          const pct = estado.total > 0 ? ((val / estado.total) * 100).toFixed(1) : "0.0";
          leitura.textContent = `${estado.labels[i]}: ${formatarBRL(val)} (${pct}%)`;
        } else {
          leitura.textContent = "Passe o mouse sobre uma fatia para dar zoom.";
        }
      },
      plugins: {
        legend: { position: "bottom", labels: { color: t.texto } },
        tooltip: {
          backgroundColor: t.tooltipBg,
          titleColor: t.tooltipTexto,
          bodyColor: t.tooltipTexto,
          padding: 12,
          titleFont: { size: 14 },
          bodyFont: { size: 13 },
          callbacks: {
            label: (ctx) => {
              const val = ctx.parsed;
              const pct = estado.total > 0 ? ((val / estado.total) * 100).toFixed(1) : "0.0";
              return `${ctx.label}: ${formatarBRL(val)} (${pct}%)`;
            },
          },
        },
      },
    },
  });

  chart.atualizarFatia = function (novosLabels, novosValores) {
    const tema = obterCoresTema();
    estado.labels = novosLabels.slice();
    estado.valores = novosValores.slice();
    estado.total = novosValores.reduce((acc, v) => acc + v, 0);
    chart.data.labels = estado.labels;
    chart.data.datasets[0].data = estado.valores;
    chart.data.datasets[0].backgroundColor = estado.labels.map(
      (_, i) => CORES_GRAFICO[i % CORES_GRAFICO.length]
    );
    chart.data.datasets[0].borderColor = tema.bordaFatia;
    chart.data.datasets[0].hoverBorderColor = tema.bordaFatia;
    chart.update();
    if (leitura) {
      leitura.textContent = "Passe o mouse sobre uma fatia para dar zoom.";
    }
  };

  window.__graficosRegistrados.push(chart);
  return chart;
}

function criarGraficoMetas(canvasId, metas, leituraId) {
  const canvas = document.getElementById(canvasId);
  if (!canvas || typeof Chart === "undefined" || !metas.length) return null;

  const t = obterCoresTema();
  const leitura = leituraId ? document.getElementById(leituraId) : null;

  const chart = new Chart(canvas, {
    type: "bar",
    data: {
      labels: metas.map((m) => m.nome),
      datasets: [
        {
          label: "Guardado",
          data: metas.map((m) => m.valor_atual),
          backgroundColor: "#10b981",
          hoverBackgroundColor: "#059669",
          borderRadius: 6,
        },
        {
          label: "Falta para a meta",
          data: metas.map((m) => m.restante),
          backgroundColor: "rgba(148, 163, 184, 0.35)",
          hoverBackgroundColor: "rgba(148, 163, 184, 0.55)",
          borderRadius: 6,
        },
      ],
    },
    plugins: [zoomHoverPlugin],
    options: {
      indexAxis: "y",
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      animation: { duration: 220 },
      onHover(event, elements) {
        canvas.style.cursor = elements.length ? "pointer" : "default";
        if (!leitura) return;
        if (elements.length) {
          const m = metas[elements[0].index];
          if (m) {
            leitura.textContent =
              `${m.nome}: ${formatarBRL(m.valor_atual)} de ${formatarBRL(m.valor_alvo)} ` +
              `(${m.progresso}%) — ${m.status_prazo}`;
          }
        } else {
          leitura.textContent = "Passe o mouse sobre uma meta para destacar e ver detalhes.";
        }
      },
      plugins: {
        legend: { position: "bottom", labels: { color: t.texto } },
        tooltip: {
          backgroundColor: t.tooltipBg,
          titleColor: t.tooltipTexto,
          bodyColor: t.tooltipTexto,
          padding: 12,
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: ${formatarBRL(ctx.parsed.x)}`,
          },
        },
      },
      scales: {
        x: {
          stacked: true,
          ticks: { color: t.texto, callback: (v) => formatarBRL(v) },
          grid: { color: t.grade },
        },
        y: {
          stacked: true,
          ticks: { color: t.texto },
          grid: { color: t.grade },
        },
      },
    },
  });

  window.__graficosRegistrados.push(chart);
  return chart;
}
