// Nachbarseiten einer GroLo-Installation, an einer Stelle für alle Seiten.
//
// Die Adressen werden aus der eigenen ab­geleitet: wird die Seite über einen Hostnamen der Form
// <dienst>.<domain> ausgeliefert, liegen die anderen Dienste unter denselben Namen (pv., settings.,
// grafana.). Lokal aufgerufen bleibt es bei den Ports des Stacks, und die Website – die keinen Port
// veröffentlicht – fehlt dann in der Leiste.
//
// Passt das Muster nicht, hier vollständige Adressen eintragen; leere Einträge werden abgeleitet.
window.GROLO_LINKS = { overview: '', settings: '', heatpump: '', grafana: '' };

window.groloNav = function (current, lang) {
  const cfg = window.GROLO_LINKS || {};
  const host = location.hostname || 'localhost';
  const secure = location.protocol === 'https:';
  const domain = host.replace(/^[^.]+\./, '');
  const sub = (name) => secure && host.split('.').length > 2 ? `https://${name}.${domain}` : null;

  const settings = cfg.settings || sub('settings') || `http://${host}:8080`;
  const grafanaBase = cfg.grafana || sub('grafana') || `http://${host}:3000`;
  const uid = current === 'heatpump' ? `wolf-${lang}` : (lang === 'de' ? 'nexa2000-de' : 'nexa2000');

  const label = {
    overview: { en: 'Overview', de: 'Übersicht' },
    settings: { en: 'Settings', de: 'Einstellungen' },
    heatpump: { en: 'Heat pump', de: 'Wärmepumpe' },
    grafana: { en: 'Grafana', de: 'Grafana' },
  };
  const items = [
    { key: 'overview', href: cfg.overview || sub('pv') },
    { key: 'settings', href: settings },
    { key: 'heatpump', href: cfg.heatpump || `${settings.replace(/\/$/, '')}/wolf.html` },
    { key: 'grafana', href: `${grafanaBase.replace(/\/$/, '')}/d/${uid}`, blank: true },
  ];
  return items
    .filter((i) => i.href && i.key !== current)
    .map((i) => ({ href: i.href, label: label[i.key][lang] || label[i.key].en, blank: !!i.blank }));
};

// Schreibt die Leiste in das übergebene Element.
window.groloRenderNav = function (el, current, lang) {
  if (!el) return;
  el.innerHTML = '';
  for (const item of window.groloNav(current, lang)) {
    const a = document.createElement('a');
    a.href = item.href;
    a.textContent = item.label;
    if (item.blank) { a.target = '_blank'; a.rel = 'noreferrer'; }
    el.appendChild(a);
  }
};
