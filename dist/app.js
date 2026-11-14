const form = document.getElementById('verifyForm');
const input = document.getElementById('jobInput');
const hero = document.getElementById('hero');
const scanner = document.getElementById('scanner');
const results = document.getElementById('results');
const tryQuery = document.getElementById('tryQuery');
const modes = [...document.querySelectorAll('.mode')];
const steps = [...document.querySelectorAll('.scan-step')];
const sources = [...document.querySelectorAll('.source-row')];
let scanTimer;

const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

modes.forEach(button => button.addEventListener('click', () => {
  modes.forEach(item => item.classList.remove('active'));
  button.classList.add('active');
  const copy = {
    verify: 'https://linkedin.com/jobs/view/3928471',
    find: 'Entry-level data jobs in NYC posted this week',
    list: 'Paste up to 10 job URLs, one per line'
  };
  input.value = copy[button.dataset.mode];
  input.focus();
}));

tryQuery.addEventListener('click', () => {
  input.value = 'Entry-level data jobs in NYC posted this week';
  modes.forEach(item => item.classList.toggle('active', item.dataset.mode === 'find'));
  input.focus();
});

async function runScan() {
  clearTimeout(scanTimer);
  results.classList.add('hidden');
  scanner.classList.remove('hidden');
  steps.forEach(step => step.className = 'scan-step');
  sources.forEach(source => { source.className = 'source-row'; source.querySelector('b').textContent = 'QUEUED'; });
  scanner.scrollIntoView({ behavior: 'smooth', block: 'start' });

  for (let index = 0; index < steps.length; index++) {
    steps[index].classList.add('active');
    if (index < 3) {
      sources[index].classList.add('active');
      sources[index].querySelector('b').textContent = 'CHECKING';
    }
    await wait(index === 3 ? 680 : 540);
    steps[index].classList.remove('active');
    steps[index].classList.add('done');
    if (index < 3) {
      sources[index].classList.remove('active');
      sources[index].classList.add('done');
      sources[index].querySelector('b').textContent = index === 2 ? 'CONFLICT' : 'VERIFIED';
      if (index === 2) sources[index].querySelector('b').style.color = 'var(--amber)';
    }
  }
  await wait(450);
  scanner.classList.add('hidden');
  results.classList.remove('hidden');
  results.scrollIntoView({ behavior: 'smooth', block: 'start' });
  document.getElementById('verifiedCount').textContent = '129';
  document.getElementById('lastVerified').textContent = 'just now';
}

form.addEventListener('submit', event => {
  event.preventDefault();
  if (!input.value.trim()) { input.focus(); return; }
  runScan();
});

document.getElementById('verifyAgain').addEventListener('click', runScan);

setInterval(() => {
  if (!results.classList.contains('hidden')) {
    const target = document.getElementById('lastVerified');
    const current = Number(target.dataset.seconds || 0) + 1;
    target.dataset.seconds = current;
    target.textContent = current < 60 ? `${current}s ago` : `${Math.floor(current / 60)}m ago`;
  }
}, 1000);
