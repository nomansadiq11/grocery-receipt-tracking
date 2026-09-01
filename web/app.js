const money = value => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(value || 0);
const monthNames = { jan: 0, feb: 1, mar: 2, apr: 3, may: 4, jun: 5, jul: 6, aug: 7, sep: 8, oct: 9, nov: 10, dec: 11 };
function parseDate(value) {
  if (!value) return null;
  const isoMatch = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (isoMatch) return new Date(Number(isoMatch[1]), Number(isoMatch[2]) - 1, Number(isoMatch[3]), 12);
  const textMatch = /^(\d{1,2})-([A-Za-z]{3})-(\d{4})$/.exec(value.trim());
  if (!textMatch || monthNames[textMatch[2].toLowerCase()] === undefined) return null;
  return new Date(Number(textMatch[3]), monthNames[textMatch[2].toLowerCase()], Number(textMatch[1]), 12);
}
const readableDate = value => {
  const parsed = parseDate(value);
  return parsed && !Number.isNaN(parsed.getTime()) ? new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric' }).format(parsed) : 'Unknown date';
};

function monthKey(value) {
  const parsed = parseDate(value);
  return parsed && !Number.isNaN(parsed.getTime()) ? `${parsed.getFullYear()}-${String(parsed.getMonth() + 1).padStart(2, '0')}` : null;
}
function render(data) {
  const receipts = data.receipts || [];
  const products = data.products || [];
  const now = new Date();
  const current = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
  const previousDate = new Date(now.getFullYear(), now.getMonth() - 1, 1);
  const previous = `${previousDate.getFullYear()}-${String(previousDate.getMonth() + 1).padStart(2, '0')}`;
  const totals = new Map();
  receipts.forEach(receipt => totals.set(monthKey(receipt.date), (totals.get(monthKey(receipt.date)) || 0) + (receipt.total || 0)));
  const currentTotal = totals.get(current) || 0;
  const previousTotal = totals.get(previous) || 0;
  const recentTotal = [...totals.entries()].filter(([key]) => key && key >= previous).reduce((sum, [, value]) => sum + value, 0);
  document.querySelector('#this-month').textContent = money(currentTotal);
  document.querySelector('#last-month').textContent = money(previousTotal);
  document.querySelector('#two-months').textContent = money(recentTotal);
  document.querySelector('#average').textContent = money(totals.size ? [...totals.values()].reduce((a, b) => a + b, 0) / totals.size : 0);
  document.querySelector('#updated').textContent = data.generated_at ? `Updated ${readableDate(data.generated_at)}` : 'Data loaded';
  document.querySelector('#receipt-count').textContent = `${receipts.length} receipt${receipts.length === 1 ? '' : 's'}`;
  const productBody = document.querySelector('#products');
  const drawProducts = query => {
    const filtered = products.filter(product => product.name.toLowerCase().includes(query.toLowerCase()));
    productBody.innerHTML = filtered.map(product => `<tr><td>${product.name}</td><td>${money(product.latest_price)}</td><td>${money(product.average_price)}</td><td>${money(product.total_spent)}</td><td>${readableDate(product.last_bought)}</td><td>${product.vendor || 'Unknown'}</td></tr>`).join('');
    document.querySelector('#products-empty').hidden = filtered.length > 0;
  };
  drawProducts('');
  document.querySelector('#product-search').addEventListener('input', event => drawProducts(event.target.value));
  document.querySelector('#receipts').innerHTML = receipts.map(receipt => `<article class="receipt"><div><p class="receipt-date">${readableDate(receipt.date)}</p><p class="receipt-vendor">${receipt.vendor || 'Unknown store'}</p><p class="receipt-items">${receipt.item_count || 0} products</p></div><strong class="receipt-total">${money(receipt.total)}</strong></article>`).join('') || '<p class="error">No receipts recorded yet.</p>';
}
fetch('data/grocery-summary.json').then(response => { if (!response.ok) throw new Error('Could not load summary'); return response.json(); }).then(render).catch(error => { document.querySelector('main').insertAdjacentHTML('beforeend', `<p class="error">${error.message}. Run the local preview from the web directory.</p>`); });
