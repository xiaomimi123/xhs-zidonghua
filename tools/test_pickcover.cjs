const fs = require("fs");
const src = fs.readFileSync("" + require("path").join(__dirname, "..", "collector", "ego", "collector.mjs") + "", "utf8");
const i = src.indexOf("function pickCover(n) {");
let depth = 0, j = i;
for (; j < src.length; j++) { if (src[j] === "{") depth++; if (src[j] === "}") { depth--; if (depth === 0) { j++; break; } } }
const pickCover = eval("(" + src.slice(i, j) + ")");
const T = [
  [{ note_image: "http://ci.xiaohongshu.com/a?x", item_image: "http://qimg/b" }, "http://ci.xiaohongshu.com/a?x"],
  [{ item_image: "http://qimg/b" }, ""],
  [{ cover: { url: "https://sns-img/c.jpg" } }, "https://sns-img/c.jpg"],
  [{ images: ["https://sns/1.jpg", "https://sns/2.jpg"] }, "https://sns/1.jpg"],
  [{ note_cover_url: "http://ci/x" }, "http://ci/x"],
  [{ display_pic: "http://ci/y" }, "http://ci/y"],
  [{ avatar_image: "http://ci/avatar", title: "t" }, ""],
  [{ cover: "not a url" }, ""],
  [{}, ""],
];
let ok = 0;
for (const [n, want] of T) { const got = pickCover(n); if (got === want) ok++; else console.log("不符", JSON.stringify(n), "得到", got, "期望", want); }
console.log("pickCover", ok + "/" + T.length);
process.exit(ok === T.length ? 0 : 1);
