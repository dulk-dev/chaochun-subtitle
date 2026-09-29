# 发布用的可选泛化改写

烧录前可以按一份词表，把字幕里已经列明的说法换成更通用的说法，方便同一条成片用不同的显示措辞发布。默认关闭。开不开由当次任务询问用户，不绑定某个平台。

这是显示层的确定性替换，用来准备要烧进画面的字幕。它不改口播，也不是内容审核方案。词表里没有的说法保持原样，不会自动整句改写。

## 行为

- 只有 `burn_subtitles.py --paraphrase-banned-terms` 会读取词表。转录、glossary 纠错和预览保存都不走这张表。
- 中文台词和章节标题用条目的 `replace.zh`，英文字幕用 `replace.en`。同一条里的拉丁名出现在中文轨时，也换成中文通用说法。
- 匹配是表内的固定词，区分长短语，不把短词切进更长的英文单词。单独的大写 `X` 会命中，并在报告里标成需要看一眼的命中；小写 `x` 不替换。
- `strategy` 只能是 `generic-paraphrase`。星号遮挡和形近字写法不会被接受。
- 报告写在草稿或 ASS 旁边，文件名是 `*.banned-term-paraphrase.json`。里面有命中次数，以及一句提醒：声音里如果仍是原来的名称，只改字幕可能不够。

Agent 可以指出词表没覆盖、但看起来像同一类名称的句子，等用户确认后再加行。不要在词表之外临时发明替换。

## 词表放哪

可直接改的种子表是仓库里的 [`config/banned_terms.json`](config/banned_terms.json)，字段见 [`config/banned_terms.schema.json`](config/banned_terms.schema.json)。`status: guess` 表示这行替换用语是建议，可以改得更顺口。

个人文件优先于种子表，查找顺序：

1. `--banned-terms`
2. `CHAOCHUN_SUBTITLE_BANNED_TERMS`
3. `~/.config/chaochun-subtitle/config.json` 里的 `banned_terms`
4. `~/.config/chaochun-subtitle/banned_terms.json`
5. 仓库 `config/banned_terms.json`

这张表和 hotwords、glossary 分开。glossary 仍只负责识别纠错，不要把发布用的通用说法写进错词表。
