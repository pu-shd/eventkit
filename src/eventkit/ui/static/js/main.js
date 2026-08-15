/* Paper Tiger — global interactions (nav toggle, search flyout) */
(function () {
  // Nav toggle
  const navToggle = document.querySelector('.pt-nav-toggle');
  const navCollapse = document.querySelector('.pt-nav-collapse');
  if (navToggle && navCollapse) {
    navToggle.addEventListener('click', () => {
      const expanded = navToggle.getAttribute('aria-expanded') === 'true';
      navToggle.setAttribute('aria-expanded', !expanded);
      navCollapse.style.display = expanded ? 'none' : 'block';
    });
  }

  // Search toggle
  const searchToggle = document.querySelector('.pt-search-toggle-inline');
  const searchFlyout = document.querySelector('#pt-search-flyout');
  if (searchToggle && searchFlyout) {
    searchToggle.addEventListener('click', (e) => {
      e.stopPropagation();
      searchFlyout.classList.toggle('is-open');
    });
    // Close on click outside
    document.addEventListener('click', (e) => {
      if (!searchFlyout.contains(e.target) && !searchToggle.contains(e.target)) {
        searchFlyout.classList.remove('is-open');
      }
    });
  }

  // Footer year
  const yearSpan = document.querySelector('#pt-year');
  if (yearSpan) {
    yearSpan.textContent = String(new Date().getFullYear());
  }
})();
