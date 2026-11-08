// Paste into the Google Sheet: Extensions > Apps Script. Then Deploy > New deployment >
// Web app, Execute as: Me, Who has access: Anyone. Copy the URL into SHEETS_WEBHOOK_URL.
const SECRET = 'PUT-THE-SAME-SECRET-AS-.env-HERE';
const COLS = ['id','mode','strategy','ticker','title','side','count','entry_price','entry_fee',
  'model_prob','market_prob','edge','opened_at','status','exit_price','exit_fee','exit_reason',
  'closed_at','pnl','outcome','notes'];

function doPost(e) {
  const body = JSON.parse(e.postData.contents);
  if (body.secret !== SECRET) return ContentService.createTextOutput('denied');
  const sh = SpreadsheetApp.getActive().getSheetByName('Trades');
  if (sh.getLastRow() === 0) sh.appendRow(COLS);
  const row = COLS.map(c => body.row[c] === null || body.row[c] === undefined ? '' : body.row[c]);
  const ids = sh.getRange(1, 1, sh.getLastRow(), 1).getValues().map(r => String(r[0]));
  const i = ids.indexOf(String(body.row.id));
  if (i > 0) sh.getRange(i + 1, 1, 1, COLS.length).setValues([row]);
  else sh.appendRow(row);
  return ContentService.createTextOutput('ok');
}
