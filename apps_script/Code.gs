// Kalshi Edge — Google side. Lives in the "Kalshi Edge — Trade Log" Sheet.
// Does 3 jobs: logs trades to the Trades tab, emails you alerts/summaries from your own
// Gmail, and checks every 30 min that the bot is alive (works even if the droplet is down).
//
// One-time setup (Extensions > Apps Script, paste this whole file, Save):
//   1. Pick setup in the function menu at the top, click Run, allow access.
//      The log shows your SECRET; you'll paste it on the droplet.
//   2. Deploy > New deployment > type Web app > Execute as: Me, Who has access: Anyone > Deploy.
//      Copy the Web app URL; you'll paste it on the droplet.

const COLS = ['id','mode','strategy','ticker','title','side','count','entry_price','entry_fee',
  'model_prob','market_prob','edge','opened_at','status','exit_price','exit_fee','exit_reason',
  'closed_at','pnl','outcome','notes'];
const STALE_MINUTES = 35;        // no heartbeat for this long = bot is down
const REALERT_HOURS = 3;         // don't repeat the "down" email more often than this

function props() { return PropertiesService.getScriptProperties(); }
function owner() { return Session.getEffectiveUser().getEmail(); }

function setup() {
  let secret = props().getProperty('SECRET');
  if (!secret) {
    secret = Utilities.getUuid().replace(/-/g, '');
    props().setProperty('SECRET', secret);
  }
  ScriptApp.getProjectTriggers().forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('checkHeartbeat').timeBased().everyMinutes(30).create();
  Logger.log('SECRET (paste this on the droplet): ' + secret);
}

function doPost(e) {
  const body = JSON.parse(e.postData.contents);
  if (!body.secret || body.secret !== props().getProperty('SECRET')) {
    return ContentService.createTextOutput('denied');
  }
  if (body.type === 'trade') logTrade(body.row);
  else if (body.type === 'heartbeat') {
    props().setProperty('LAST_BEAT', String(Date.now()));
    if (props().getProperty('DOWN_SENT')) {
      props().deleteProperty('DOWN_SENT');
      MailApp.sendEmail(owner(), 'Kalshi Edge: bot is back up', 'Heartbeat received again at ' + new Date());
    }
  }
  else if (body.type === 'email') MailApp.sendEmail(owner(), body.subject, body.body);
  return ContentService.createTextOutput('ok');
}

function logTrade(r) {
  const sh = SpreadsheetApp.getActive().getSheetByName('Trades');
  if (sh.getLastRow() === 0) sh.appendRow(COLS);
  const row = COLS.map(c => r[c] === null || r[c] === undefined ? '' : r[c]);
  const ids = sh.getRange(1, 1, sh.getLastRow(), 1).getValues().map(x => String(x[0]));
  const i = ids.indexOf(String(r.id));
  if (i > 0) sh.getRange(i + 1, 1, 1, COLS.length).setValues([row]);
  else sh.appendRow(row);
}

function checkHeartbeat() {
  const last = Number(props().getProperty('LAST_BEAT') || 0);
  if (!last) return;                                   // bot never connected yet
  const mins = (Date.now() - last) / 60000;
  const sent = Number(props().getProperty('DOWN_SENT') || 0);
  if (mins > STALE_MINUTES && Date.now() - sent > REALERT_HOURS * 3600000) {
    MailApp.sendEmail(owner(), 'Kalshi Edge: BOT IS DOWN',
      'No heartbeat for ' + Math.round(mins) + ' minutes.\n\n' +
      'Check in the DigitalOcean droplet console:\n  systemctl status kalshi-edge-bot\n\n' +
      'If the droplet itself is off, power it on at cloud.digitalocean.com.');
    props().setProperty('DOWN_SENT', String(Date.now()));
  }
}
