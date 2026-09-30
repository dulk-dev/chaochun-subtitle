# 发布用的可选泛化改写

烧录前可以按一份词表，把字幕里已经列明的说法换成更通用的说法，方便同一条成片用不同的显示措辞发布。默认关闭。开不开由当次任务询问用户，不绑定某个平台。

这是显示层的确定性替换，用来准备要烧进画面的字幕。它不改口播，也不是内容审核方案。词表里没有的说法保持原样，不会自动整句改写。

## 行为

- 只有 `burn_subtitles.py --paraphrase-banned-terms` 会读取词表。转录、glossary 纠错和预览保存都不走这张表。
- 中文台词和章节标题用该行的 `zh`，英文字幕用 `en`。同一条里的拉丁名出现在中文轨时，也换成中文说法。
- 匹配是表内的固定词。更长的名称优先，例如 `Claude Code` 不会被拆成 `Claude`。仓库主表不收录单独的字母 `X`，只写 `X平台`、Twitter、推特；单字母行会被拒绝。
- 星号遮挡、形近字和零宽字符不能当作替换结果。
- 报告写在草稿或 ASS 旁边，文件名是 `*.banned-term-paraphrase.json`。里面有命中次数，以及一句提醒：声音里如果仍是原来的名称，只改字幕可能不够。

Agent 可以指出词表没覆盖、但看起来像同一类名称的句子，等用户确认后再加行。不要在词表之外临时发明替换。

## 词表放哪

可直接改的主表是仓库里的 [`config/banned_terms.json`](config/banned_terms.json)。它是一个 JSON 数组，每行只有三个字段，见 [`config/banned_terms.schema.json`](config/banned_terms.schema.json)：

```json
[
  { "term": "Codex", "zh": "AI编程工具", "en": "AI coding tool" }
]
```

`term` 是要替换的原词，`zh` 和 `en` 是烧进中英文字幕的说法。不需要 id、备注或词组数组。多一个说法就多写一行，两行可以用同一组 `zh` / `en`。

个人文件优先于种子表，查找顺序：

1. `--banned-terms`
2. `CHAOCHUN_SUBTITLE_BANNED_TERMS`
3. `~/.config/chaochun-subtitle/config.json` 里的 `banned_terms`
4. `~/.config/chaochun-subtitle/banned_terms.json`
5. 仓库 `config/banned_terms.json`

这张表和 hotwords、glossary 分开。glossary 仍只负责识别纠错，不要把发布用的通用说法写进错词表。

## 表里有什么

主表收录国外 Agent 产品名（Claude、Claude Code、ChatGPT、GPT、Gemini、Cursor 等），以及发布时容易被当成站外引流的平台和产品名（淘宝、京东、抖音、快手、小红书、微博、飞书、钉钉、豆包、微信、企业微信、QQ 等）。开启替换后这些行都会生效，仍然不绑定某一个发布平台。要增删，直接改词表。

英文里的 `cursor` 如果只是「光标」，这次替换打开时也会换成 AI 编程工具。不需要这条时，从个人词表删掉该行，或本次不要开启替换。

## 不进主表

- 加群、扫码这类整句不单独立条。平台名本身在表里，句子里出现该名字就会替换。
- 形近字、火星文、零宽字符。
- 单独的字母 `X`。
