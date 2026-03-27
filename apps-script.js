/**
 * Wine Cellar — Google Apps Script webhook
 * ─────────────────────────────────────────
 * Deploy as a Web App (Execute as: Me, Who has access: Anyone).
 * Paste the deployment URL into the APPS_SCRIPT_URL env var on Railway.
 *
 * Supported actions
 * ─────────────────
 * add_wine        — append a row to the Cellar sheet
 * add_drunk       — append a row to the Drunk sheet
 * update_quantity — find wine by name+vintage in Cellar, decrement qty;
 *                   delete the row if qty reaches 0
 * delete_wine     — find wine by name+vintage in Cellar, delete the row
 *
 * Cellar sheet columns (A–J):
 *   name | vintage | region | grape | winery | rating | price | qty | dateAdded | notes
 *
 * Drunk sheet columns (A–M):
 *   name | vintage | region | grape | winery | rating | price | qty | dateAdded | notes |
 *   dateDrunk | myScore | myNotes
 */

// Column indices (1-indexed, for getRange)
var CELLAR_COL = { name:1, vintage:2, region:3, grape:4, winery:5, rating:6, price:7, qty:8, dateAdded:9, notes:10 };
var DRUNK_COL  = { name:1, vintage:2, region:3, grape:4, winery:5, rating:6, price:7, qty:8, dateAdded:9, notes:10, dateDrunk:11, myScore:12, myNotes:13 };

function doPost(e) {
  try {
    var data   = JSON.parse(e.postData.contents);
    var action = data.action;
    var ss     = SpreadsheetApp.getActiveSpreadsheet();

    if (action === 'add_wine') {
      var sheet = ss.getSheetByName('Cellar');
      sheet.appendRow([
        data.name       || '',
        data.vintage    || '',
        data.region     || '',
        data.grape      || '',
        data.winery     || '',
        data.rating     || '',
        data.price      || '',
        data.quantity   || 1,
        data.date_added || '',
        data.notes      || '',
      ]);
      return jsonOk('Wine added to cellar');
    }

    if (action === 'add_drunk') {
      var sheet = ss.getSheetByName('Drunk');
      sheet.appendRow([
        data.name       || '',
        data.vintage    || '',
        data.region     || '',
        data.grape      || '',
        data.winery     || '',
        data.rating     || '',
        data.price      || '',
        data.quantity   || 1,
        data.date_added || '',
        data.notes      || '',
        data.date_drunk || '',
        data.my_score   || '',
        data.my_notes   || '',
      ]);
      return jsonOk('Tasting logged');
    }

    if (action === 'update_quantity') {
      var sheet    = ss.getSheetByName('Cellar');
      var rowIndex = findRow(sheet, data.name, data.vintage);
      if (rowIndex === -1) {
        return jsonError('Wine not found in Cellar');
      }
      var qtyCell = sheet.getRange(rowIndex, CELLAR_COL.qty);
      var newQty  = (parseInt(qtyCell.getValue()) || 0) + (data.quantity_change || -1);
      if (newQty <= 0) {
        sheet.deleteRow(rowIndex);
        return jsonOk('Wine removed (quantity reached 0)');
      }
      qtyCell.setValue(newQty);
      return jsonOk('Quantity updated to ' + newQty);
    }

    if (action === 'delete_wine') {
      var sheet    = ss.getSheetByName('Cellar');
      var rowIndex = findRow(sheet, data.name, data.vintage);
      if (rowIndex === -1) {
        return jsonError('Wine not found in Cellar');
      }
      sheet.deleteRow(rowIndex);
      return jsonOk('Wine deleted');
    }

    return jsonError('Unknown action: ' + action);

  } catch (err) {
    return jsonError(err.toString());
  }
}

/**
 * Find the 1-indexed sheet row whose name and vintage match.
 * Returns -1 if not found.
 */
function findRow(sheet, name, vintage) {
  var values = sheet.getDataRange().getValues();
  for (var i = 1; i < values.length; i++) {  // i=1 skips header row
    var rowName    = String(values[i][0]).trim();
    var rowVintage = String(values[i][1]).trim();
    if (rowName === String(name || '').trim() &&
        rowVintage === String(vintage || '').trim()) {
      return i + 1;  // convert to 1-indexed row number
    }
  }
  return -1;
}

function jsonOk(message) {
  return ContentService
    .createTextOutput(JSON.stringify({ status: 'ok', message: message }))
    .setMimeType(ContentService.MimeType.JSON);
}

function jsonError(message) {
  return ContentService
    .createTextOutput(JSON.stringify({ status: 'error', message: message }))
    .setMimeType(ContentService.MimeType.JSON);
}
