const menuToggle = document.querySelector('.menu-toggle');
const siteNav = document.querySelector('.site-nav');

menuToggle?.addEventListener('click', () => {
  const isOpen = menuToggle.getAttribute('aria-expanded') === 'true';
  menuToggle.setAttribute('aria-expanded', String(!isOpen));
  menuToggle.setAttribute('aria-label', isOpen ? 'Open navigation' : 'Close navigation');
  menuToggle.title = isOpen ? 'Open navigation' : 'Close navigation';
  siteNav?.classList.toggle('is-open', !isOpen);
});

siteNav?.querySelectorAll('a').forEach((link) => {
  link.addEventListener('click', () => {
    menuToggle?.setAttribute('aria-expanded', 'false');
    menuToggle?.setAttribute('aria-label', 'Open navigation');
    if (menuToggle) menuToggle.title = 'Open navigation';
    siteNav.classList.remove('is-open');
  });
});

const screenDetails = {
  billing: {
    src: './assets/billing-dashboard.png',
    alt: 'Actual screenshot of the Bill Bot billing controls and item total.',
    route: 'billing',
    caption: 'Billing controls / model confidence / current total',
  },
  admin: {
    src: './assets/admin-access.png',
    alt: 'Actual screenshot of the Bill Bot admin login protecting model training and price management.',
    route: 'admin/login',
    caption: 'Admin access / protected training and catalog controls',
  },
};

const screenImage = document.querySelector('#screen-image');
const screenRoute = document.querySelector('#screen-route');
const screenCaption = document.querySelector('#screen-caption');

document.querySelectorAll('.screen-tab').forEach((tab) => {
  tab.addEventListener('click', () => {
    const details = screenDetails[tab.dataset.screen];
    if (!details || !screenImage) return;

    document.querySelectorAll('.screen-tab').forEach((item) => {
      const selected = item === tab;
      item.classList.toggle('is-active', selected);
      item.setAttribute('aria-pressed', String(selected));
    });

    screenImage.classList.add('is-changing');
    screenImage.src = details.src;
    screenImage.alt = details.alt;
    if (screenRoute) screenRoute.textContent = details.route;
    if (screenCaption) screenCaption.textContent = details.caption;
    screenImage.addEventListener('load', () => screenImage.classList.remove('is-changing'), { once: true });
  });
});

const revealItems = document.querySelectorAll('[data-reveal]');
if ('IntersectionObserver' in window) {
  const revealObserver = new IntersectionObserver((entries, observer) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add('is-visible');
      observer.unobserve(entry.target);
    });
  }, { threshold: 0.12 });
  revealItems.forEach((item) => revealObserver.observe(item));
} else {
  revealItems.forEach((item) => item.classList.add('is-visible'));
}