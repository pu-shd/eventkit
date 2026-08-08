/* Paper Tiger — minimal slider behavior.
 * Add `data-pt-hero` to a .pt-hero block and load this file once.
 * Honours prefers-reduced-motion: pauses autoplay and shortens fades. */
(function () {
  const heroes = document.querySelectorAll('.pt-hero[data-pt-hero]');
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

  heroes.forEach((hero) => {
    const slides = Array.from(hero.querySelectorAll('.pt-hero__slide'));
    const total  = slides.length;
    if (!total) return;

    const $cur = hero.querySelector('.pt-hero__current');
    const $tot = hero.querySelector('.pt-hero__total');
    if ($tot) $tot.textContent = String(total);

    let index = slides.findIndex(el => el.classList.contains('is-current'));
    if (index < 0) { index = 0; slides[0].classList.add('is-current'); }

    let timer = null;
    const interval = Number(hero.dataset.intervalMs || 8000);

    function show(i) {
      slides[index].classList.remove('is-current');
      index = (i + total) % total;
      slides[index].classList.add('is-current');
      if ($cur) $cur.textContent = String(index + 1);
    }
    function next() { show(index + 1); }
    function prev() { show(index - 1); }

    function start() { if (!timer && !reduce) timer = setInterval(next, interval); }
    function stop()  { clearInterval(timer); timer = null; }

    hero.addEventListener('click', (e) => {
      const t = e.target.closest('[data-action]');
      if (!t) return;
      if (t.dataset.action === 'next') { next(); }
      if (t.dataset.action === 'prev') { prev(); }
      if (t.dataset.action === 'toggle') {
        if (timer) { stop(); t.textContent = '▶'; }
        else       { start(); t.textContent = '⏸'; }
      }
    });
    hero.addEventListener('mouseenter', stop);
    hero.addEventListener('mouseleave', start);
    hero.addEventListener('focusin',    stop);
    hero.addEventListener('focusout',   start);

    start();
  });
})();
