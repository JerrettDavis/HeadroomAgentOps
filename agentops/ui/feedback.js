/* Feedback is explicit: background refreshes never create notifications. */
(() => {
  const region = document.createElement('aside');
  region.id = 'action-notification';
  region.hidden = true;
  region.setAttribute('aria-label', 'Action notification');
  const message = document.createElement('span');
  const announcement = document.createElement('span');
  announcement.className = 'sr-only';
  announcement.setAttribute('role', 'status');
  announcement.setAttribute('aria-live', 'polite');
  announcement.setAttribute('aria-atomic', 'true');
  document.body.append(announcement);
  const close = document.createElement('button');
  close.type = 'button'; close.textContent = '×';
  close.setAttribute('aria-label', 'Dismiss notification');
  region.append(message, close); document.body.append(region);
  let timer, announcementTimer, origin;
  function dismiss() {
    clearTimeout(timer);
    if (region.contains(document.activeElement) && origin?.isConnected) origin.focus({preventScroll:true});
    region.hidden = true;
  }
  close.addEventListener('click', dismiss);
  region.addEventListener('mouseenter', () => clearTimeout(timer));
  region.addEventListener('focusin', () => clearTimeout(timer));
  function schedule() {
    clearTimeout(timer);
    if (region.dataset.kind !== 'error' && !region.matches(':hover') && !region.contains(document.activeElement)) timer = setTimeout(dismiss, 7000);
  }
  region.addEventListener('mouseleave', schedule);
  region.addEventListener('focusout', () => setTimeout(schedule, 0));
  window.agentopsFeedback = {
    notify(text, kind = 'success') {
      clearTimeout(timer);
      clearTimeout(announcementTimer);
      if (!region.contains(document.activeElement)) origin = document.activeElement;
      region.dataset.kind = kind;
      message.textContent = String(text);
      region.hidden = false;
      announcement.textContent = '';
      announcementTimer = setTimeout(() => { announcement.textContent = String(text); }, 30);
      schedule();
    },
    busy(element, value) {
      if (!element) return;
      if (value) element.setAttribute('aria-busy', 'true');
      else element.removeAttribute('aria-busy');
    },
    enter(element) {
      if (!element || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
      element.getAnimations().filter(animation => animation.id === 'agentops-enter').forEach(animation => animation.cancel());
      element.animate([{opacity:.65},{opacity:1}], {duration:160,easing:'ease-out',id:'agentops-enter'});
    }
  };
  document.addEventListener('agentops-view', event => {
    const ids = {reviews:'reviews-workspace',priority:'priority-view',benchmarks:'benchmark-view',updates:'maintenance-view','main-ci':'maintenance-view'};
    window.agentopsFeedback.enter(document.getElementById(ids[event.detail?.view]));
  });
})();
