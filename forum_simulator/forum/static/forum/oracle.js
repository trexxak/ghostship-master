(() => {
  function parseJsonScript(id) {
    const node = document.getElementById(id);
    if (!node) {
      return [];
    }
    try {
      const text = node.textContent || "";
      return JSON.parse(text);
    } catch (error) {
      // eslint-disable-next-line no-console
      console.warn("Failed to parse JSON payload", id, error);
      return [];
    }
  }

  const draws = parseJsonScript('oracle-draw-data');
  const scrubberEntries = parseJsonScript('oracle-scrubber-data');
  const canvas = document.querySelector('[data-oracle-canvas]');
  const ctx = canvas ? canvas.getContext('2d') : null;
  const scrubber = document.querySelector('[data-oracle-scrubber]');
  const meta = document.querySelector('[data-oracle-meta]');

  function findScrubberEntry(tick) {
    return scrubberEntries.find((entry) => entry.tick === tick) || {};
  }

  function formatSpecials(specials) {
    if (!specials) {
      return '';
    }
    const parts = [];
    if (specials.seance) {
      const details = specials.seance_details || {};
      parts.push(`Seance: ${details.label || 'World Event'}`);
    }
    if (specials.omen) {
      const details = specials.omen_details || {};
      parts.push(`Omen: ${details.label || 'Incident'}`);
    }
    return parts.join(' \u2013 ');
  }

  function renderMeta(index) {
    if (!meta) {
      return;
    }
    if (!draws.length) {
      meta.innerHTML = '<p class="meta">No oracle activity recorded yet.</p>';
      return;
    }
    const draw = draws[Math.min(Math.max(index, 0), draws.length - 1)];
    const trace = findScrubberEntry(draw.tick);
    const specials = formatSpecials(draw.specials || draw.alloc?.specials);
    const notes = Array.isArray(draw.notes) ? draw.notes.join(' | ') : '';
    const eventCount = Array.isArray(trace.events) ? trace.events.length : 0;
    const decisionCount = typeof trace.decision_count === 'number' ? trace.decision_count : 0;
    meta.innerHTML = [
      `<h3>Tick ${draw.tick}</h3>`,
      '<dl class="oracle-meta__grid">',
      `<div><dt>Energy</dt><dd>${draw.energy} \u2192 ${draw.energy_prime}</dd></div>`,
      `<div><dt>Seed</dt><dd>${draw.seed ?? '\u2014'}</dd></div>`,
      `<div><dt>Card</dt><dd>${draw.card || '\u2014'}</dd></div>`,
      `<div><dt>Rolls</dt><dd>${Array.isArray(draw.rolls) ? draw.rolls.join(' + ') : '\u2014'}</dd></div>`,
      `<div><dt>Trace</dt><dd>${decisionCount} decisions, ${eventCount} events</dd></div>`,
      specials ? `<div class="oracle-meta__span"><dt>Special</dt><dd>${specials}</dd></div>` : '',
      notes ? `<div class="oracle-meta__span"><dt>Notes</dt><dd>${notes}</dd></div>` : '',
      '</dl>',
    ].join('');
  }

  function drawTimeline() {
    if (!ctx || !canvas) {
      return;
    }
    const width = canvas.width;
    const height = canvas.height;
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = '#0b1610';
    ctx.fillRect(0, 0, width, height);

    if (!draws.length) {
      ctx.fillStyle = 'rgba(198, 225, 206, 0.7)';
      ctx.font = '16px "Inter", sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('No draws recorded', width / 2, height / 2);
      return;
    }

    const energies = draws.flatMap((entry) => [entry.energy || 0, entry.energy_prime || 0]);
    const maxEnergy = Math.max(...energies, 1);
    const minEnergy = Math.min(...energies, 0);
    const range = Math.max(maxEnergy - minEnergy, 1);

    const plotPadding = 20;
    const stepX = draws.length > 1 ? (width - plotPadding * 2) / (draws.length - 1) : 0;

    function projectY(value) {
      const normalised = (value - minEnergy) / range;
      return height - plotPadding - normalised * (height - plotPadding * 2);
    }

    ctx.strokeStyle = 'rgba(102, 204, 153, 0.35)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(plotPadding, projectY(minEnergy));
    ctx.lineTo(width - plotPadding, projectY(minEnergy));
    ctx.moveTo(plotPadding, projectY(maxEnergy));
    ctx.lineTo(width - plotPadding, projectY(maxEnergy));
    ctx.stroke();

    ctx.lineWidth = 2;
    ctx.strokeStyle = '#6fe4b3';
    ctx.beginPath();
    draws.forEach((entry, index) => {
      const x = plotPadding + stepX * index;
      const y = projectY(entry.energy || 0);
      if (index === 0) {
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, y);
      }
    });
    ctx.stroke();

    ctx.strokeStyle = '#5ab5f0';
    ctx.beginPath();
    draws.forEach((entry, index) => {
      const x = plotPadding + stepX * index;
      const y = projectY(entry.energy_prime || 0);
      if (index === 0) {
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, y);
      }
    });
    ctx.stroke();
  }

  function configureScrubber() {
    if (!scrubber) {
      return;
    }
    const maxIndex = Math.max(draws.length - 1, 0);
    scrubber.min = '0';
    scrubber.max = String(maxIndex);
    scrubber.value = String(maxIndex);
    scrubber.disabled = !draws.length;
  }

  if (scrubber) {
    scrubber.addEventListener('input', (event) => {
      const target = event.currentTarget;
      const index = parseInt(target.value, 10) || 0;
      renderMeta(index);
    });
  }

  configureScrubber();
  renderMeta(Math.max(draws.length - 1, 0));
  drawTimeline();
})();
