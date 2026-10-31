"""Heuristic detector for text that addresses an AI ("ignore the previous instructions ...").

It is an *advisory* signal with two readers.  In the agent, a fetched page or a search result that trips it is marked
and the model is told to treat it as data.  In the publishing pipeline, an update that was researched from such a page,
or whose own text trips it, is not published automatically: it waits for a person.

The real defence is structural: the agent's tools are read-only, a proposal must pass the schema and grounding checks,
and the publisher replays and validates it again.  This list only decides when a person is asked to look, so it is
tuned from both sides: the moves an injected instruction makes (below: English, Chinese, and the best-known sentence in
a few more languages) and the ordinary sentences of vendor documentation and of the portal's own zh-TW notes that must
*not* trip it (tests/test_web.py holds both lists).  Where the two pull apart, ordinary documentation wins: API guides
say "send the following request to https://...", "requests to the LLM must ..." and "System: You are ..." on every
page, and a detector that stops every update researched from them is switched off by the people it interrupts.  It
will miss a determined author - "no hit" never means "clean" - and it will now and then stop an innocent page;
neither breaks anything.
"""

from __future__ import annotations

import re
import unicodedata

#: the agent's own tools whose names do not occur in ordinary documentation (vendors have tools called web_search)
_TOOLS = r"(?:submit_proposal|record_evidence|fetch_page|find_in_page|lookup_portal)"
_Q = r"[`'\"“”‘’「」『』]?"  # an optional quote mark around a name
_AI = r"(?:ai|a\.i\.|llms?|gpts?|chatbots?|(?:large )?language models?)"
#: a reader that is addressed by what it does, or by its name
_AUTOMATED = r"(?:(?:automated|autonomous|research|web|browsing|coding|ai) ){1,2}(?:agents?|assistants?|crawlers?|bots?|tools?|systems?)"
_NAMED = r"(?:claude|chatgpt|gpt|gemini|copilot|assistant|agent|bot|ai)"
_ROLE = r"(?:agents?|assistants?|models?|systems?|bots?|crawlers?|tools?)"
_START = r"(?:^|[\n.!?。！？:：]) ?"  # where a sentence (or a line) begins
_DET = r"(?:(?:all|any|each|every|of|the|your|these|those) ){0,4}"
_ORDERS_EN = r"(?:instructions?|prompts?|rules|directions|guidelines|guidance|directives?|orders|commands)"
_EARLIER = r"(?:previous|prior|above|earlier|preceding|foregoing|original|initial|existing|system)"
# "the gateway did not ignore the previous instructions parameter" reports what software did: no order is given
_NOT_DENIED = r"(?<!not )(?<!n't )(?<!never )(?<!did )(?<!does )(?<!doesn't )"
# What an exfiltration instruction asks for is what the agent holds.  "The following request", "your data" and "all
# messages" are what every API guide tells a developer to send somewhere.
_HELD = (r"(?:everything(?: you (?:have|know|found|collected|were told))?|(?:this|the|your) conversation|"
         r"(?:(?:all|any) (?:of )?)?(?:the|your) (?:system prompt|instructions|secrets?|credentials|api keys?|access keys?|tokens|findings|memory|context)|"
         r"(?:all|any) (?:of )?(?:the )?(?:secrets|credentials|api keys|access keys)|the contents of (?:your|this) (?:conversation|context|memory))")

# English.  A space in a pattern stands for any run of blanks, line breaks, hyphens or underscores (see _compile), so
# "ignore-all-previous-instructions" and a sentence broken across two lines are the same text.
_ENGLISH = [
    rf"{_NOT_DENIED}\b(?:ignore|disregard|forget|override|bypass) {_DET}{_EARLIER} {_ORDERS_EN}\b",
    rf"{_NOT_DENIED}\b(?:ignore|disregard|forget|override|bypass) {_DET}{_ORDERS_EN} (?:above|before|so far|given (?:to you )?(?:above|earlier|before|so far)|you (?:were|have been) given)\b",
    rf"\b(?:do not|don't|stop|no longer|never) follow(?:ing)? {_DET}(?:{_EARLIER} )?{_ORDERS_EN} (?:above|before|so far|given (?:to you )?(?:above|earlier|before|so far)|you (?:were|have been) given)\b",
    rf"\b(?:do not|don't|stop|no longer|never) follow(?:ing)? {_DET}{_EARLIER} {_ORDERS_EN}\b",
    # ("Forget everything you know about prompt engineering" is how an article begins)
    r"\bforget (?:everything|all) (?:you (?:were|have been) told|you know(?! about)|i (?:said|told you)|above|previous|that came before)\b",
    # "Ignore the above and instead ...", "Disregard everything above." - an order, at the start of a sentence, to drop
    # what stands before it ("you can safely ignore the above warning" is advice about a warning)
    rf"{_START}(?:please )?(?:ignore|disregard|forget) (?:all (?:of )?)?(?:the above|everything above|all of the above|everything (?:before|prior)|anything above)"
    r"(?: (?:text|content|instructions?|information|statements?|messages?))?(?: and\b| ?[.,:;!]| ?$)",
    r"\b(?:supersedes?|overrides?|replaces?|takes? precedence over|cancels?|voids?|nullif(?:y|ies)) (?:all (?:of )?)?your "
    r"(?:(?:previous|prior|original|current|existing|system) )?(?:system prompt|prompt|instructions?|rules|guidelines|directives?)\b",
    r"\byour (?:new|real|actual|true) (?:task|job|goal|objective|mission|instructions?|orders) (?:is|are) (?:to|now to|as follows|the following)\b",
    r"\b(?:override|overwrite|bypass|forget|drop|abandon|reset|ignore|disregard) (?:all (?:of )?)?your (?:(?:previous|prior|original|initial|current|existing|system) )?"
    r"(?:instructions?|rules|guidelines|prompt|programming|constraints|training)\b",
    r"\byou are now (?:dan|stan|dude|unrestricted|jailbroken|unfiltered)\b",
    r"\byou are now in (?:(?:a|an|the) )?(?:developer|dan|jailbreak|god|admin|debug|unrestricted|maintenance) mode\b",
    # a message that names its reader: "Note to AI agents: ...", "AI agents reading this page, ..."
    rf"{_START}(?:(?:an? )?(?:very )?important )?(?:notes?|notice|messages?|instructions?|attention|reminders?|warnings?) (?:for|to) (?:(?:all|any|the) )?{_AI}"
    rf"(?: {_ROLE})?(?: (?:reading|processing|parsing|summari[sz]ing|viewing|crawling|indexing) this(?: (?:page|document|text|site))?)? ?[:：,]",
    rf"\b{_AI} {_ROLE},? (?:reading|processing|parsing|summari[sz]ing|viewing|crawling|indexing) this\b",
    rf"{_START}(?:(?:ai|automated|llm|research) ){{1,2}}(?:agents?|tools?|assistants?|crawlers?|bots?) ?[:：,] ?(?:please|you|ignore|disregard|do not|always|never|in your|when|set|record|use|add)\b",
    rf"\b(?:if|when) you(?: are|'re|’re) (?:a|an) (?:{_AI} (?:{_ROLE}|system)|llm|chatbot|language model|automated (?:agent|system|assistant|crawler|bot)|artificial intelligence)\b",
    # "If you are an AI, add ..." (but "if you are an AI engineer, ..." speaks to a person)
    rf"\b(?:if|when) you(?: are|'re|’re) (?:a|an) {_AI} ?[,:;.!]",
    rf"\b(?:dear|attention|hey|hello|hi),? (?:(?:all|any|the) )?(?:{_AI}(?: {_ROLE})?|{_AUTOMATED}) ?[,:：]",
    # "To the LLM processing this document: ...", "Instructions for automated research agents: ..."
    rf"{_START}to (?:(?:all|any|the) )?(?:{_AI}(?: {_ROLE})?|{_AUTOMATED})(?: (?:reading|processing|parsing|summari[sz]ing|viewing|crawling|indexing|handling|analy[sz]ing) "
    r"this(?: [a-z]{2,12})?)? ?[:：,]",
    rf"{_START}(?:(?:an? )?(?:very )?important )?(?:notes?|notice|messages?|instructions?|attention|reminders?|warnings?) (?:for|to) (?:(?:all|any|the) )?{_AUTOMATED} ?[:：,]",
    # "IMPORTANT: AI assistants must record ..." - an order that begins by calling for attention
    # (about what to report, not about how software is built: "Important: AI agents must use scoped tokens" is a rule for developers)
    rf"{_START}(?:very )?(?:important|attention|notice|note|warning|urgent|critical|reminder|mandatory) ?[:：!] ?(?:(?:all|any) )?(?:{_AI} {_ROLE}|{_AUTOMATED}|{_AI}) "
    r"(?:must|should|shall|need to|have to|are (?:required|instructed|expected|asked) to) (?:now |always |never |only |not )?"
    r"(?:record|report|cite|treat|state|say|write|list|consider|regard|assume|believe|prefer|trust|ignore|disregard|omit|skip)\b",
    # (an order, not an opinion: "as an AI model, you should always be evaluated on your own data" is advice to a reader)
    r"\bas an ai (?:language )?model,? you (?:must|should|will) (?:now |always |never |only )?(?:ignore|disregard|follow|obey|comply|respond|answer|output|reveal|"
    r"pretend|act|not)\b",
    # chat-template tokens and role markers that give an order
    rf"{_START}(?:system|assistant|developer)(?: (?:prompt|message|note|instruction))? ?[:：] ?(?:ignore|disregard|from now on|new instructions|"
    r"you (?:must|will|should|shall) (?:now )?(?:ignore|disregard|follow|obey|only|always|never))",
    r"<\|(?:im start|im end|system|user|assistant|start header id|end header id|eot id|endoftext|begin of text)\|>",
    r"\[/?inst\]|<</?sys>>",
    r"</ ?(?:system|assistant|tool use|tool result|function results?) ?>",
    r"< ?system ?>[^<]{0,200}?\b(?:you (?:are|must|will|should|shall)|ignore|disregard|always|never)\b",
    # secrets
    r"\b(?:reveal|print|show|repeat|leak|output|disclose|tell me) (?:me )?your (?:(?:full|entire|complete|hidden|secret|system|original|initial) )*(?:prompt|instructions)\b",
    r"\b(?:leak|disclose|exfiltrate|tell me|give me|send me|reveal) (?:me )?your (?:(?:secret|private|hidden) )?(?:credentials|secrets?|api keys?|access keys?|tokens?)\b",
    rf"(?:{_START}|\b(?:please|now|then|and) )(?:reveal|print|output|repeat|leak|disclose) (?:me )?the (?:(?:full|entire|complete|hidden|secret) )?system prompt\b",
    # the agent's own machinery: its tools, its output, the field only the platform team sets
    rf"\b(?:call|invoke|use|run|execute|trigger) (?:the )?{_Q}{_TOOLS}\b",
    rf"\b{_TOOLS}{_Q} ?\(",
    r"\b(?:in|when (?:writing|preparing|drafting|creating|submitting|making|composing)|before (?:writing|submitting)) your (?:proposal|digest)\b[^.\n]{0,40}?"
    r"\b(?:set|write|record|change|replace|put|use|add|include|state|cite|omit|remove|skip|mention)\b",
    r"\b(?:set|mark|change|update|make|flip|switch) [^.\n]{0,30}?\bplatformstatus\b",
    r"\bplatformstatus\b[^.\n]{0,20}?(?:\b(?:to|as)\b|=) ?['\"`]?(?:approved|ga|recommended|supported)\b",
    rf"\b(?:send|post|upload|exfiltrate|forward|transmit|email) {_HELD}\b.{{0,40}}?\b(?:to|at) https?://",
    r"\bdo not (?:tell|inform|mention (?:this )?to|alert|notify) the (?:user|operator|reviewer|human)\b",
    r"\b(?:do not|don't|never) (?:mention|disclose|reveal|report|show) [^.\n]{0,30}? to the (?:operator|reviewer|human|maintainer|approver)s?\b",
    # Japanese: "AIアシスタントへ：...", "言語モデルへの指示："
    r"(?:AI|人工知能|言語モデル|LLM) ?(?:アシスタント|エージェント|モデル|ボット)?(?:の皆さん|各位)?(?:へ(?:の(?:指示|注意|お知らせ|メッセージ))?|に告ぐ) ?[:：、,]",
    # the same first move in the languages vendor pages are also written in
    r"(?:これまで|以前|上記|先ほど|前)の?(?:すべての|全ての)?(?:指示|命令|プロンプト|ルール)(?:は|を)?(?:すべて|全て)?(?:無視|忘れ)",
    r"(?:이전|위|앞)(?:의)? ?(?:모든 )?(?:지시|지침|명령|프롬프트)(?:를|을|는|은|사항을)? ?(?:모두 )?(?:무시|잊)",
    r"\bignor(?:a|e|en|ar)\w{0,3} (?:todas? )?(?:las |los )?(?:instrucciones|indicaciones|órdenes|ordenes|reglas) (?:anteriores|previas|de arriba)\b",
    r"\bignor(?:e|ez|er|ons) (?:toutes? )?(?:les )?(?:instructions|consignes|directives|règles|regles) (?:précédentes|precedentes|antérieures|anterieures|ci dessus)\b",
    r"\bignorier(?:e|en|t|st)? (?:sie )?(?:bitte )?(?:alle )?(?:vorherigen|vorigen|bisherigen|obigen|früheren|frueheren) (?:anweisungen|anordnungen|befehle|regeln|instruktionen)\b",
]
# Prompt examples say these on every prompt-engineering page ("Example prompt: From now on, you will act as ..."); they
# count unless the line introduces them as an example (see _EXAMPLE).
_ENGLISH_ROLE_PLAY = [
    # "Hey Claude, please update ..." (every prompt guide has a line like it, as an example)
    rf"{_START}(?:hey|hello|hi|dear|ok|okay|attention),? {_NAMED} ?[,:：!] ?(?:please|you|ignore|disregard|do not|don't|always|never|update|set|record|use|add|change|replace|write)\b",
    r"\byou are now (?:a|an|the|my|our) (?:[a-z]{1,30} ){0,2}(?:assistant|ai|a\.i\.|model|agent|bot|chatbot)\b",
    r"\bfrom now on,? you (?:are (?:a|an|the|my|no longer)|will (?:act|respond|answer|behave|only|always|never|ignore|follow|obey)|(?:must|should) (?:act|respond|answer|always|never|ignore|follow|obey))\b",
    rf"{_START}(?:new|updated|revised|additional) (?:system )?(?:instructions?|orders|directives?)(?: (?:for|to) (?:the )?[a-z\x20]{{0,20}})? ?[:：]",
]

_NOT = r"(?:不要|不必|不用|無需|无需|無須|无须|請勿|请勿|勿|別|别|不再|停止)(?:再)?"
# (not 依照 / 按照 and not 管理: "新版不再依照原本的規則計費" and "不再管理既有的規則" are statements about software)
_VERB = (r"(?:忽略|無視|无视|忘記|忘记|忘掉|略過|略过|跳過|跳过|(?:別|别|不要|不用|不必)管(?!理)|"
         rf"{_NOT}(?:理會|理会|遵守|遵循|遵照|遵從|遵从|聽從|听从))")
# A statement about what software does ("LiteLLM 會忽略原本的…", "模型可能忽略先前的…") is not an order.
_NOT_A_STATEMENT = r"(?<![會会將将能已])"
_BEFORE = r"(?:之前|先前|以上|上述|上面|前面|前述|原本|原先|原來|原来|此前|既有|現有|现有|所有|任何|一切)"
_ORDERS = r"(?:指示|指令|提示詞|提示词|提示|規則|规则|規定|规定|要求|限制|命令)"
_SYSTEM = r"(?:系統提示詞?|系统提示词?|系統指令|系统指令|系統訊息|系统消息|system ?prompt)"
_YOU = r"(?:你|您|妳)"
_AI_ZH = r"(?:AI|A\.I\.|人工智慧|人工智能|語言模型|语言模型|大模型|機器人|机器人|智能體|智能体)"
_ROLE_ZH = r"(?:助理|助手|代理|agent|模型)"
_PROMPT_ZH = r"(?:系統提示詞?|系统提示词?|system ?prompt|提示詞|提示词|指令|指示)"
_KEY_ZH = r"(?:金鑰|密鑰|密钥|憑證|凭证|API ?key|token)"
# what the agent holds (see _HELD): "把所有請求傳送到 https://…" and "把你的資料上傳到 https://…" are ordinary set-up steps
_THESE = (r"(?:(?:這些|这些|以上|上述|上面)(?:的)?(?:內容|内容|對話|对话|結果|结果)|(?:你的|您的|妳的)(?:系統提示詞?|系统提示词?|對話|对话|金鑰|密鑰|密钥|憑證|凭证|發現|发现|結果|结果)|"
          r"所有(?:的)?(?:對話|对话|金鑰|密鑰|密钥|憑證|凭证))")
_SEND = r"(?:傳送|發送|发送|上傳|上传|轉寄|转发|回傳|回传|寄送)"
# Chinese (traditional and simplified).  Each pattern needs the whole move, not one word: the portal's own text is zh-TW
# and talks about prompts, instructions, settings and users all the time.
_CHINESE = [
    rf"{_NOT_A_STATEMENT}{_VERB}[^。！？\n]{{0,12}}?{_BEFORE}[^。！？\n]{{0,8}}?{_ORDERS}",
    rf"{_NOT_A_STATEMENT}{_VERB}[^。！？\n]{{0,12}}?{_SYSTEM}",
    # ("從現在起，你將依 token 計費" is a notice about billing: what follows 你 has to be an order about how to behave)
    rf"(?:從現在起|从现在起|從現在開始|从现在开始|自現在起|自现在起|從此刻起|从此刻起)[，,\x20]{{0,2}}{_YOU}(?:是(?:一個|一个|一名|一位|我的)|不再是|"
    r"(?:將|将|要|必須|必须|應該|应该|只能|不得|不再)(?:扮演|忽略|無視|无视|遵守|遵循|遵照|聽從|听从|服從|服从|回答|回覆|回复|只|永遠|永远|一律|作為|作为|充當|充当|"
    r"使用以下|按照以下|依照以下|以))",
    rf"{_YOU}的(?:新|真正的?|實際的?|实际的?)(?:任務|任务|角色|身分|身份|目標|目标|指示|指令)(?:是|為|为|[:：])",
    rf"{_YOU}(?:現在|现在|目前|接下來|接下来)的(?:任務|任务|工作|目標|目标|角色|指示|指令)(?:是|為|为|[:：])",
    # "請在你的提案中把日期寫成 ..." - what the agent itself is about to write
    rf"(?:在|於|于)?{_YOU}的 ?(?:提案|摘要|digest|proposal) ?(?:中|裡|里|內|内|上)[^。！？\n]{{0,12}}?(?:把|將|将|加入|加上|寫|写|改|填|使用|引用|刪|删|移除|省略|不要)",
    # "各位語言模型：請 ..." - a message that names its reader and goes on with an order
    rf"(?:^|[\n。！？!?:：] ?)(?:各位|所有|任何|致|給|给|請|请)? ?{_AI_ZH} ?{_ROLE_ZH}? ?(?:們|们)? ?[:：，,] ?(?:請|请|你|您|務必|务必|必須|必须|應|应|不要|忽略|無視|无视)",
    r"(?:^|[\n。！？!?] ?|以下是|以下為|以下为|這是|这是)(?:新的|更新的|更新後的|更新后的|修訂的|修订的|最新的|額外的|额外的)(?:系統|系统)?(?:指示|指令) ?[:：]",
    rf"(?:^|[\n。！？!?:：] ?)(?:致|給|给|請|请)? ?{_AI_ZH} ?{_ROLE_ZH}? ?(?:請注意|请注意|注意|須知|须知) ?[:：，,]",
    rf"如果{_YOU}是(?:一個|一个|一名|一位)? ?{_AI_ZH}",
    rf"(?:顯示|显示|洩漏|泄漏|洩露|泄露|輸出|输出|印出|說出|说出|告訴我|告诉我|貼出|贴出){_YOU}的 ?{_PROMPT_ZH}",
    rf"(?:洩漏|泄漏|洩露|泄露|說出|说出|告訴我|告诉我|貼出|贴出){_YOU}的 ?{_KEY_ZH}",
    rf"{_NOT_A_STATEMENT}(?:把|將|将){_YOU}的 ?(?:{_PROMPT_ZH}|{_KEY_ZH})[^。！？\n]{{0,16}}?(?:輸出|输出|顯示|显示|印出|貼|贴|寫|写|傳|传|洩|泄|說出|说出|告訴|告诉|提供)",
    r"不要(?:告訴|告诉|通知)(?:使用者|用戶|用户|操作者|審核者|审核者|人類|人类)",
    r"不要(?:讓|让)(?:使用者|用戶|用户|操作者|審核者|审核者|人類|人类)(?:知道|發現|发现|察覺|察觉|看到|看見|看见)",
    rf"(?:呼叫|調用|调用|使用|執行|执行|觸發|触发) ?{_Q}{_TOOLS}",
    r"(?:把|將|将) ?platformstatus ?(?:設為|设为|設成|设成|改為|改为|改成|標成|标成|標為|标为|標記為|标记为)",
    rf"{_SEND}{_THESE}[^。！？\n]{{0,30}}?(?:到|至|給|给) ?https?://",
    rf"{_NOT_A_STATEMENT}(?:把|將|将){_THESE}[^。！？\n]{{0,30}}?(?:{_SEND}|寄|貼|贴|POST)[^。！？\n]{{0,8}}?(?:到|至|給|给) ?https?://",
]
_CHINESE_ROLE_PLAY = [
    rf"{_YOU}(?:現在|现在)是(?:一個|一个|一名|一位)",
]


def _compile(patterns: list[str]) -> re.Pattern:
    def spaced(pattern: str) -> str:
        marker = "\x00"  # `` ?`` (an optional blank) stays optional; \x20 inside a character class is left alone
        return pattern.replace(" ?", marker).replace(" ", r"[ \n_-]").replace(marker, r"[ \n_-]?")

    return re.compile("|".join(f"(?:{spaced(p)})" for p in patterns), re.I)


_RX = _compile(_ENGLISH + _CHINESE)
_RX_ROLE_PLAY = _compile(_ENGLISH_ROLE_PLAY + _CHINESE_ROLE_PLAY)
#: what the line says before a role-play sentence when that sentence is shown, not said
_EXAMPLE = re.compile(r"(?:\bexamples?\b|\bsample\b|\be\.g\.|\bfor instance\b|\bsuch as\b|\bprompts?\b|\btemplate\b|範例|范例|示例|例如|比如|譬如|提示詞|提示词)[^.。!?！？\n]{0,60}\Z", re.I)

# Text can be written so that it reads the same to a model and slips past a pattern: letters of another width or
# alphabet, characters that take no room, odd blanks.  ``plain`` undoes that before the patterns look.
#: Characters that are not white space to Unicode and show as an empty stretch all the same: the blank Braille cell, a
#: notehead without a note, fillers and modifiers that have nothing to modify, the placeholder for an embedded object.
#: A run of them is padding of exactly the length its author wants.
BLANK_LOOKING = "\u2800\uFFFC\U0001D159\U00016FE4" + "".join(map(chr, [*range(0x1DA9B, 0x1DAA0), *range(0x1DAA1, 0x1DAB0)]))
_BLANKS = "\t\r\f\v     　ㅤᅟᅠﾠ" + "".join(map(chr, range(0x2000, 0x200B))) + BLANK_LOOKING
# Cyrillic and Greek letters that look like Latin ones (the usual way to hide an English word from a filter)
_LOOKALIKE = {
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s", "һ": "h", "ԁ": "d", "ӏ": "l",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X", "І": "I", "Ј": "J",
    "ο": "o", "α": "a", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ρ": "p", "τ": "t", "υ": "u", "χ": "x",
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Χ": "X",
}
#: Unicode categories that take no room on a page: format characters (zero-width joiners, direction marks, tag
#: characters), combining marks (variation selectors, the grapheme joiner), controls, private-use and unassigned code points
_NO_ROOM = {"Cf", "Mn", "Me", "Cc", "Cs", "Co", "Cn"}
#: Code points that display as nothing although their category says "letter" or "mark" (Unicode: Default_Ignorable):
#: hangul fillers, the grapheme joiner, Mongolian and other variation selectors
_IGNORABLE = ({0x034F, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x180B, 0x180C, 0x180D, 0x180F, 0x3164, 0xFFA0}
              | set(range(0xFE00, 0xFE10)) | set(range(0xE0100, 0xE01F0)))


class _Fold(dict):
    """A translation table that is worked out one code point at a time, the first time the code point is seen."""

    def __missing__(self, code: int):
        ch = chr(code)
        if ch == "\n":
            out: int | str | None = code
        elif ch in _BLANKS or ch.isspace():
            out = " "
        elif ch in _LOOKALIKE:
            out = _LOOKALIKE[ch]
        elif unicodedata.category(ch) in _NO_ROOM:
            out = None
        else:
            out = code
        self[code] = out
        return out


class _Shown(dict):
    """Like ``_Fold``, for text that is going to be *shown*: only what a reader cannot see goes (format characters,
    surrogates, private-use and unassigned code points, the invisible "letters"); controls, the Unicode line
    separators and the symbols that show as an empty stretch (``BLANK_LOOKING``) become a blank; accents and every
    visible letter stay as they are."""

    def __missing__(self, code: int):
        ch = chr(code)
        category = unicodedata.category(ch)
        if ch in "\n\t":
            out: int | str | None = code
        elif category in ("Cf", "Cs", "Co", "Cn") or code in _IGNORABLE:
            out = None
        elif category in ("Cc", "Zl", "Zp") or ch in BLANK_LOOKING:
            out = " "
        else:
            out = code
        self[code] = out
        return out


_FOLD = _Fold()
_SHOWN = _Shown()
_SPACES = re.compile(r" {2,}")
_BREAKS = re.compile(r" ?\n[ \n]*")
# Runs of the characters a pattern treats as "a separator" are folded to one: a line of dashes, a ruler of underscores
# and an empty list item say nothing, and matching through thousands of them costs the square of their number.
_RULES = re.compile(r"[ \n_-]{2,}")
_TAGS = re.compile("[\U000e0000-\U000e007f]+")
HIDDEN_NOTE = "[hidden text written in invisible Unicode tag characters]"


def visible(text: str) -> str:
    """``text`` without the characters that display as nothing (zero-width and direction marks, soft hyphens, tag
    characters, fillers): what a reader sees is what a check must look at - and what a message must contain."""
    return text.translate(_SHOWN)


def hidden_text(text: str) -> str:
    """What ``text`` says in Unicode tag characters: they display as nothing, and a model reads them as ASCII."""
    runs = []
    for run in _TAGS.findall(text):
        runs.append("".join(chr(ord(ch) - 0xE0000) if 0xE0020 <= ord(ch) <= 0xE007E else " " for ch in run))
    return " ".join(" ".join(runs).split())


def plain(text: str) -> str:
    """``text`` as the patterns should see it.

    Compatibility forms are folded (full-width letters become ASCII), look-alike Cyrillic and Greek letters become the
    Latin ones, characters that take no room are dropped, every kind of blank is one space and a line break is one
    ``\\n`` (a pattern may care where a line starts).
    """
    text = unicodedata.normalize("NFKC", text).translate(_FOLD)
    return _BREAKS.sub("\n", _SPACES.sub(" ", text)).strip()


def _separated(text: str) -> str:
    """``plain`` text with every run of separators (blanks, line breaks, hyphens, underscores) as ONE character: a line
    break if the run had one, else the run's first character."""
    return _RULES.sub(lambda m: "\n" if "\n" in m.group(0) else m.group(0)[0], text)


def scan(text: str, max_hits: int = 3) -> list[str]:
    """Up to ``max_hits`` excerpts of ``text`` that read like an instruction to an AI (each with some context)."""
    if not isinstance(text, str) or not text:
        return []
    hits: list[str] = []
    if _TAGS.search(text):
        hits.append(f"{HIDDEN_NOTE} {hidden_text(text)[:160]}".strip())
    seen = _separated(plain(text))
    found = [(m.start(), m.end()) for m in _RX.finditer(seen)]
    for m in _RX_ROLE_PLAY.finditer(seen):
        line_start = seen.rfind("\n", 0, m.start()) + 1
        if not _EXAMPLE.search(seen, max(line_start, m.start() - 120), m.start()):
            found.append((m.start(), m.end()))
    for begin, end in sorted(found):
        if len(hits) >= max_hits:
            break
        start = max(0, begin - 40)
        hits.append(" ".join(seen[start : end + 40][:200].split()))
    return hits[:max_hits]
