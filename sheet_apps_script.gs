/**
 * 股票學習導師 —— Google 試算表後端（Apps Script Web App）
 * ────────────────────────────────────────────────
 * 部署步驟：
 * 1. 開一個新的 Google 試算表
 * 2. 擴充功能 → Apps Script，把這整段貼進去
 * 3. 修改下面 TOKEN 成你自己設的一組密碼（要跟 Render 的 SHEET_TOKEN 一樣）
 * 4. 部署 → 新增部署作業 → 類型「網頁應用程式」
 *    - 執行身分：我
 *    - 誰可以存取：所有人
 * 5. 複製「網頁應用程式 URL」→ 貼到 Render 的 SHEET_WEBAPP_URL
 *
 * 會自動建立 3 個分頁：state / lessons / hypotheses
 */

var TOKEN = "換成你自己的密碼";   // ← 必改，且要跟 app.py 的 SHEET_TOKEN 一致

var HEADERS = {
  state:      ["user", "mode", "pending", "updated_at"],
  lessons:    ["user", "created_at", "source_type", "title",
               "ai_explanation", "user_understanding", "corrected_note"],
  hypotheses: ["user", "created_at", "hypothesis_id", "category", "status",
               "hypothesis", "rule_json", "source_idea"],
};

function sheet_(name) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(name);
  if (!sh) {
    sh = ss.insertSheet(name);
    sh.appendRow(HEADERS[name]);
  }
  return sh;
}

function rowsAsObjects_(sh) {
  var data = sh.getDataRange().getValues();
  if (data.length < 2) return [];
  var head = data[0], out = [];
  for (var i = 1; i < data.length; i++) {
    var o = {};
    for (var j = 0; j < head.length; j++) o[head[j]] = data[i][j];
    out.push(o);
  }
  return out;
}

function appendByHeader_(sh, obj) {
  var head = sh.getDataRange().getValues()[0];
  var row = head.map(function (h) { return obj[h] !== undefined ? obj[h] : ""; });
  sh.appendRow(row);
}

function doPost(e) {
  var out = { ok: false };
  try {
    var p = JSON.parse(e.postData.contents);
    if (p.token !== TOKEN) return json_({ ok: false, error: "bad token" });
    var a = p.action;

    if (a === "getstate") {
      var rows = rowsAsObjects_(sheet_("state"));
      for (var i = 0; i < rows.length; i++) {
        if (String(rows[i].user) === String(p.user))
          return json_({ ok: true, mode: rows[i].mode, pending: rows[i].pending });
      }
      return json_({ ok: true, mode: "chat", pending: "" });
    }

    if (a === "setstate") {
      var sh = sheet_("state");
      var data = sh.getDataRange().getValues();
      for (var r = 1; r < data.length; r++) {
        if (String(data[r][0]) === String(p.user)) {
          sh.getRange(r + 1, 1, 1, 4).setValues([[p.user, p.mode, p.pending, now_()]]);
          return json_({ ok: true });
        }
      }
      sh.appendRow([p.user, p.mode, p.pending, now_()]);
      return json_({ ok: true });
    }

    if (a === "add_lesson") {
      appendByHeader_(sheet_("lessons"), p);
      return json_({ ok: true });
    }

    if (a === "add_hypothesis") {
      appendByHeader_(sheet_("hypotheses"), p);
      return json_({ ok: true });
    }

    if (a === "count_hypotheses") {
      var rows = rowsAsObjects_(sheet_("hypotheses")).filter(function (x) {
        return String(x.user) === String(p.user);
      });
      return json_({ ok: true, count: rows.length });
    }

    if (a === "list_lessons" || a === "list_hypotheses") {
      var name = a === "list_lessons" ? "lessons" : "hypotheses";
      var since = p.since || "0000-00-00";
      var rows = rowsAsObjects_(sheet_(name)).filter(function (x) {
        if (String(x.user) !== String(p.user)) return false;
        return String(x.created_at).slice(0, 10) >= since;
      });
      return json_({ ok: true, rows: rows });
    }

    return json_({ ok: false, error: "unknown action" });
  } catch (err) {
    return json_({ ok: false, error: String(err) });
  }
}

function now_() {
  return Utilities.formatDate(new Date(), "Asia/Taipei", "yyyy-MM-dd HH:mm");
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
