# 发布用的可选泛化改写

烧录前可以按一份词表，把字幕里已经列明的说法换成更通用的说法，方便同一条成片用不同的显示措辞发布。默认关闭。开不开由当次任务询问用户，不绑定某个平台。

这是显示层的确定性替换，用来准备要烧进画面的字幕。它不改口播，也不是内容审核方案。词表里没有的说法保持原样，不会自动整句改写。

## 行为

- 只有 `burn_subtitles.py --paraphrase-banned-terms` 会读取词表。转录、glossary 纠错和预览保存都不走这张表。
- 中文台词和章节标题用条目的 `replace.zh`，英文字幕用 `replace.en`。同一条里的拉丁名出现在中文轨时，也换成中文通用说法。
- 匹配是表内的固定词，区分长短语，不把短词切进更长的英文单词。仓库主表不匹配单独的字母 `X`，只匹配 `X平台`、Twitter、推特。
- `strategy` 只能是 `generic-paraphrase`。星号遮挡、形近字和零宽字符不在这张表里。
- 报告写在草稿或 ASS 旁边，文件名是 `*.banned-term-paraphrase.json`。里面有命中次数，以及一句提醒：声音里如果仍是原来的名称，只改字幕可能不够。

Agent 可以指出词表没覆盖、但看起来像同一类名称的句子，等用户确认后再加行。不要在词表之外临时发明替换。

## 词表放哪

可直接改的主表是仓库里的 [`config/banned_terms.json`](config/banned_terms.json)，字段见 [`config/banned_terms.schema.json`](config/banned_terms.schema.json)。当前行是研究整理的初始表，`status` 为 `confirmed`。个人词表里如果某行还没定稿，可以标 `guess`。

个人文件优先于种子表，查找顺序：

1. `--banned-terms`
2. `CHAOCHUN_SUBTITLE_BANNED_TERMS`
3. `~/.config/chaochun-subtitle/config.json` 里的 `banned_terms`
4. `~/.config/chaochun-subtitle/banned_terms.json`
5. 仓库 `config/banned_terms.json`

这张表和 hotwords、glossary 分开。glossary 仍只负责识别纠错，不要把发布用的通用说法写进错词表。

## 不进主表

下面这些不做精确词匹配，也不要写进默认词表：

- 引流、留联系方式、进群、扫码这一类，要看整句上下文，不能靠固定词替换。
- 形近字、火星文、零宽字符。
- Claude、Claude Code、ChatGPT、GPT、Gemini、Cursor。这一轮没有足够依据，先不收录。
- 抖音、小红书这类国内平台的跨平台叫法，要先知道成片准备发到哪里，再决定要不要改。那是以后的可选扩展，不放进现在这份自动启用的主表。
