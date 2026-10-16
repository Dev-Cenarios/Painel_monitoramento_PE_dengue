/* ============================================================================
 * Dengue PE | www/dengue.js
 * 1. Binding de saida "dengue.echarts" (painel/echarts.py).
 * 2. Destaque do municipio no mapa (mensagem 'dengue_map_highlight').
 * 3. Popover de "Opcoes" que fecha ao clicar fora ou com Esc.
 * ========================================================================== */
(function () {
  'use strict';

  // -- revive funcoes JS e marcadores do payload ------------------------------
  function reviver(x) {
    if (Array.isArray(x)) return x.map(reviver);
    if (x && typeof x === 'object') {
      if (Object.prototype.hasOwnProperty.call(x, '__js__')) {
        // eslint-disable-next-line no-new-func
        return new Function('return (' + x.__js__ + ');')();
      }
      var out = {};
      Object.keys(x).forEach(function (k) { out[k] = reviver(x[k]); });
      return out;
    }
    return x;
  }

  // Malhas: baixadas e registradas uma vez por pagina (promessa por nome).
  var malhas = {};
  function registrarMalha(m) {
    var chave = m.nome + '|' + m.url;
    if (!malhas[chave]) {
      malhas[chave] = fetch(m.url).then(function (r) { return r.json(); })
        .then(function (geo) { echarts.registerMap(m.nome, geo); });
    }
    return malhas[chave];
  }

  function instancia(el) {
    var alvo = el.querySelector('.ec-grafico');
    return alvo ? echarts.getInstanceByDom(alvo) : null;
  }

  // -- 1. binding de saida ----------------------------------------------------
  var binding = new Shiny.OutputBinding();
  $.extend(binding, {
    find: function (scope) { return $(scope).find('.dengue-echarts'); },

    renderValue: function (el, dados) {
      var alvo = el.querySelector('.ec-grafico');
      var msg = el.querySelector('.ec-mensagem');
      if (!dados) return;

      if (dados.__mensagem__) {
        var c0 = instancia(el);
        if (c0) c0.dispose();
        alvo.style.display = 'none';
        msg.textContent = dados.__mensagem__;
        msg.style.display = 'flex';
        return;
      }
      msg.style.display = 'none';
      alvo.style.display = 'block';

      var mapa = dados.__mapa__;
      var clique = dados.__clique__;
      delete dados.__mapa__;
      delete dados.__clique__;
      var opcao = reviver(dados);
      // Eixos numericos sem formatter proprio: separadores pt-BR (10.000; 2,5).
      ['xAxis', 'yAxis'].forEach(function (k) {
        [].concat(opcao[k] || []).forEach(function (e) {
          if (!e || e.type !== 'value') return;
          e.axisLabel = e.axisLabel || {};
          if (!e.axisLabel.formatter) {
            e.axisLabel.formatter = function (v) { return v.toLocaleString('pt-BR'); };
          }
        });
      });

      var aplicar = function () {
        var chart = instancia(el) || echarts.init(alvo, null, { renderer: 'canvas' });
        chart.setOption(opcao, true);   // notMerge: o option e sempre completo
        chart.off('click');
        if (clique) {
          // setTimeout: tira o setInputValue de dentro do evento do zrender,
          // senao o primeiro clique da sessao as vezes nao chega ao servidor.
          chart.on('click', function (p) {
            if (p && p.name && p.seriesName !== '__ref__') {
              var nome = (p.data && p.data.cod) ? p.data.cod : p.name;
              setTimeout(function () {
                Shiny.setInputValue(clique, nome, { priority: 'event' });
              }, 60);
            }
          });
        }
        if (!el._observador && window.ResizeObserver) {
          el._observador = new ResizeObserver(function () {
            var c = instancia(el);
            if (c) c.resize();
          });
          el._observador.observe(el);
        }
        $(el).trigger('dengue:renderizado');
      };

      if (mapa) registrarMalha(mapa).then(aplicar);
      else aplicar();
    },

    renderError: function (el, err) {
      var msg = el.querySelector('.ec-mensagem');
      var alvo = el.querySelector('.ec-grafico');
      if (!err || !err.message) { msg.style.display = 'none'; return; }   // req() silencioso
      alvo.style.display = 'none';
      msg.textContent = err.message;
      msg.style.display = 'flex';
    },

    clearError: function (el) {
      el.querySelector('.ec-mensagem').style.display = 'none';
      el.querySelector('.ec-grafico').style.display = 'block';
    }
  });
  Shiny.outputBindings.register(binding, 'dengue.echarts');

  // -- 2. destaque do municipio no mapa ----------------------------------------
  // Estado 'select' do ECharts (persistente, casado pelo nome = geocodigo) +
  // opacidade reduzida no resto da serie, sem re-renderizar o mapa. Reafirmado
  // 4x em ~480 ms para cobrir a corrida com uma re-renderizacao em andamento.
  Shiny.addCustomMessageHandler('dengue_map_highlight', function (m) {
    var n = 0;
    function aplicar() {
      n++;
      var el = document.getElementById(m.id);
      var chart = el ? instancia(el) : null;
      if (chart) {
        var opt = chart.getOption();
        var nDados = (opt.series && opt.series[0] && opt.series[0].data) ? opt.series[0].data.length : 0;
        var temSel = (m.name !== null && m.name !== undefined && m.name !== '');
        chart.setOption({ series: [{
          selectedMode: 'single',
          itemStyle: { opacity: temSel ? 0.3 : 1 },
          select: { itemStyle: { opacity: 1 } }
        }] });
        if (nDados > 0) {
          var todos = [];
          for (var i = 0; i < nDados; i++) todos.push(i);
          chart.dispatchAction({ type: 'unselect', seriesIndex: 0, dataIndex: todos });
        }
        if (temSel) chart.dispatchAction({ type: 'select', seriesIndex: 0, name: String(m.name) });
      }
      if (n < 4) setTimeout(aplicar, 120);
    }
    aplicar();
  });

  // -- 3. popover que fecha ao clicar fora / Esc --------------------------------
  function dentroDoSeletor(t) {
    return t && t.closest && t.closest('.selectize-dropdown, .selectize-control, .irs');
  }
  function gatilhoDe(tip) {
    return tip.id ? document.querySelector('[aria-describedby="' + tip.id + '"]') : null;
  }
  function esconder(tip) {
    var trg = gatilhoDe(tip);
    var inst = (trg && window.bootstrap && bootstrap.Popover) ? bootstrap.Popover.getInstance(trg) : null;
    var host = (trg && trg.closest) ? trg.closest('bslib-popover') : null;
    if (inst) inst.hide();
    else if (host && typeof host.hide === 'function') host.hide();
    else if (trg) trg.click();
  }
  document.addEventListener('click', function (e) {
    var abertos = document.querySelectorAll('.popover.show');
    if (!abertos.length || dentroDoSeletor(e.target)) return;
    abertos.forEach(function (tip) {
      var trg = gatilhoDe(tip);
      if (tip.contains(e.target)) return;
      if (trg && (trg === e.target || trg.contains(e.target))) return;
      esconder(tip);
    });
  });
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    document.querySelectorAll('.popover.show').forEach(esconder);
  });
})();
