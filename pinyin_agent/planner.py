import re
import logging
from functools import lru_cache
from pathlib import Path
from typing import List
from .domain import HAN, Chunk, validate_plan


def readings(text):
    from pypinyin import lazy_pinyin, Style
    prepare_readings()
    return tuple(lazy_pinyin(text, style=Style.NORMAL, errors="default"))


@lru_cache(maxsize=1)
def prepare_readings():
    import json
    from pypinyin import load_phrases_dict
    overrides=json.loads(Path(__file__).with_name('pronunciations.json').read_text(encoding='utf-8'))
    load_phrases_dict({word:[[syllable] for syllable in syllables] for word,syllables in overrides.items()})


@lru_cache(maxsize=4096)
def allowed_readings(char):
    from pypinyin import pinyin, Style
    return frozenset(pinyin(char, style=Style.NORMAL, heteronym=True)[0])


def validate_readings(chunk):
    for char, syllable in zip(chunk.text, chunk.syllables):
        if syllable not in allowed_readings(char):
            raise ValueError("模型给出的读音不在字典中：%s / %s" % (char, syllable))


def correct_model_readings(pieces, contextual_readings, progress):
    """Keep exact model text; rebuild malformed phonetics from dictionary context."""
    corrected,position=[],0
    for piece in pieces:
        syllables=list(piece.syllables)
        if len(syllables)!=len(piece.text) or not all(isinstance(item,str) for item in syllables):
            syllables=list(contextual_readings[position:position+len(piece.text)])
            progress("已用词典补齐拼音：%s → %s" % (piece.text,"'".join(syllables)))
        for index,(char,syllable) in enumerate(zip(piece.text,syllables)):
            if syllable not in allowed_readings(char):
                replacement=contextual_readings[position+index]
                if replacement not in allowed_readings(char):
                    raise ValueError("词典无法提供可用的拼音读音："+char)
                syllables[index]=replacement
                progress("已用词典校正读音：%s / %s → %s" % (char,syllable,replacement))
        corrected.append(Chunk(piece.text,tuple(syllables)))
        position+=len(piece.text)
    return corrected


@lru_cache(maxsize=1)
def local_tokenizer():
    import jieba
    jieba.setLogLevel(logging.ERROR)
    tokenizer=jieba.Tokenizer()
    cache=Path(__file__).resolve().parents[1]/'runs'
    cache.mkdir(exist_ok=True)
    tokenizer.tmp_dir=str(cache)
    tokenizer.cache_file='pinyin-jieba-0.42.1.cache'
    with Path(__file__).with_name('lexicon.txt').open('rb') as dictionary:
        tokenizer.load_userdict(dictionary)
    return tokenizer


def local_han(text):
    result,offset = [],0
    full_readings=readings(text)
    for word in local_tokenizer().cut(text,HMM=False):
        result.append(Chunk(word, full_readings[offset:offset+len(word)]))
        offset+=len(word)
    return result


@lru_cache(maxsize=1)
def hint_frequencies():
    import jieba
    # Domain words are deliberately boosted for segmentation, but that boost
    # says nothing about their rank in a system IME. Use unmodified word counts.
    values={}
    with Path(jieba.__file__).with_name('dict.txt').open(encoding='utf-8') as source:
        for line in source:
            word,frequency,*_=line.split()
            if 2<=len(word)<=8 and HAN.fullmatch(word):values[word]=int(frequency)
    return values


@lru_cache(maxsize=1)
def character_hints():
    # Pre-index frequent whole words once; keep the original segmentation.
    choices={}
    for word,frequency in hint_frequencies().items():
        if len(word)==2 and frequency>=500 and HAN.fullmatch(word):
            if word[0] not in choices or frequency>choices[word[0]][0]:
                choices[word[0]]=(frequency,word)
    return {char:Chunk(word,readings(word)) for char,(_,word) in choices.items()}


@lru_cache(maxsize=1)
def suffix_character_hints():
    choices={}
    for word,frequency in hint_frequencies().items():
        if len(word)==2 and frequency>=500 and HAN.fullmatch(word):
            if word[-1] not in choices or frequency>choices[word[-1]][0]:
                choices[word[-1]]=(frequency,word)
    return {char:Chunk(word,readings(word)) for char,(_,word) in choices.items()}


def character_hint(chunk,allow_suffix=False):
    if len(chunk.text)!=1:return None
    return character_hints().get(chunk.text) or (suffix_character_hints().get(chunk.text) if allow_suffix else None)


@lru_cache(maxsize=4096)
def phrase_hint(text):
    options=[(frequency,word.startswith(text),word) for word,frequency in hint_frequencies().items()
             if len(text)<len(word)<=8 and text in word and (frequency>=10 or word in hint_idioms())]
    if not options:return None
    word=max(options)[2]
    return Chunk(word,readings(word))


@lru_cache(maxsize=1)
def hint_idioms():
    import jieba
    with Path(jieba.__file__).with_name('dict.txt').open(encoding='utf-8') as source:
        return frozenset(parts[0] for line in source if len(parts:=line.split())==3 and parts[2]=='i')


def selection_hint(chunk):
    preferred=preferred_hints().get(chunk.text)
    return preferred or (character_hint(chunk,allow_suffix=True) if len(chunk.text)==1 else phrase_hint(chunk.text))


@lru_cache(maxsize=1)
def preferred_hints():
    import json
    data=json.loads(Path(__file__).with_name('typing_hints.json').read_text(encoding='utf-8'))
    return {target:Chunk(word,readings(word)) for target,word in data.items()
            if isinstance(word,str) and target in word and word!=target and len(word)<=8 and HAN.fullmatch(word)}


def make_plan(source, model=None, progress=lambda message: None) -> List[Chunk]:
    if not source:
        raise ValueError("请先输入原文。")
    if "\r" in source or "\x00" in source:
        raise ValueError("请将换行统一为 LF；文本不能含有 NUL。")
    chunks, position = [], 0
    for match in HAN.finditer(source):
        if match.start() > position:
            chunks.append(Chunk(source[position:match.start()]))
        if not model:
            # Local processing has no request-size boundary: keep words intact.
            chunks.extend(local_han(match.group()))
            position=match.end()
            continue
        # Use the full Han run so a model's artificial chunk boundaries cannot
        # turn the dictionary fallback's 银行 into standalone 行 / xing.
        contextual_readings=readings(match.group()) if model else ()
        # Bounded requests; preserve surrounding context for polyphonic words.
        for offset in range(0, len(match.group()), 120):
            target = match.group()[offset:offset + 120]
            if model:
                progress("Qwen 正在规划：" + target[:24])
                rows = model.plan(target, source[max(0, match.start()-50):match.end()+50])
                try:
                    pieces=[]
                    for row in rows:
                        if not isinstance(row,dict):raise ValueError("分词片段格式错误。")
                        syllables=row.get("syllables",())
                        if not isinstance(syllables,(list,tuple)):syllables=()
                        pieces.append(Chunk(row["text"],tuple(syllables)))
                    # Validate the immutable source independently of model phonetics.
                    # Missing/merged syllables must not prevent dictionary repair.
                    if (not pieces or any(not piece.text or not piece.chinese for piece in pieces)
                            or "".join(piece.text for piece in pieces)!=target):
                        raise ValueError("切分结果改写或遗漏了原文，已拒绝执行。")
                    pieces=correct_model_readings(pieces,contextual_readings[offset:offset+len(target)],progress)
                    for piece in pieces:
                        validate_readings(piece)
                    validate_plan(target,pieces)
                except (KeyError, TypeError, ValueError) as error:
                    raise ValueError("Qwen 计划验证失败：%s" % error) from error
                chunks.extend(pieces)
        position = match.end()
    if position < len(source):
        chunks.append(Chunk(source[position:]))
    validate_plan(source, chunks)
    return chunks


def make_hybrid_plan(source,model=None,progress=lambda message:None):
    """Dictionary boundaries protect words; optional model advice cannot block input."""
    baseline=make_plan(source)
    if model is None:return baseline
    if len(source)>400:
        progress('长文本使用本地词典，避免等待过长的模型输出。')
        return baseline
    try:
        progress('本地分词已完成，Qwen 正在核对词组与读音……')
        rows=model.plan(source,source)
        proposed=[]
        for row in rows:
            if not isinstance(row,dict) or not isinstance(row.get('text'),str):
                raise ValueError('模型片段格式错误')
            syllables=row.get('syllables',())
            if not isinstance(syllables,(list,tuple)):syllables=()
            proposed.append(Chunk(row['text'],tuple(syllables)))
        if not proposed or ''.join(c.text for c in proposed)!=source or any(not c.text for c in proposed):
            raise ValueError('模型改写或遗漏原文')
        dictionary=[]
        protected=set()
        boundaries={0,len(source)}
        position=0
        for chunk in baseline:
            dictionary.extend(chunk.syllables if chunk.chinese else [None]*len(chunk.text))
            if chunk.chinese:
                protected.update(range(position+1,position+len(chunk.text)))
            else:
                boundaries.update((position,position+len(chunk.text)))
            position+=len(chunk.text)
        phonetics=list(dictionary)
        position=0
        for piece in proposed:
            end=position+len(piece.text)
            if piece.chinese:
                fixed=correct_model_readings([piece],dictionary[position:end],progress)[0]
                phonetics[position:end]=fixed.syllables
            elif HAN.search(piece.text):raise ValueError('模型混合了汉字与符号')
            boundaries.add(end)
            position=end
        # Local pronunciation overrides take priority over either engine.
        import json
        overrides=json.loads(Path(__file__).with_name('pronunciations.json').read_text(encoding='utf-8'))
        for word,syllables in overrides.items():
            for match in re.finditer(re.escape(word),source):phonetics[match.start():match.end()]=syllables
        positions=sorted(boundaries-protected)
        result=[]
        for start,end in zip(positions,positions[1:]):
            text=source[start:end]
            if HAN.fullmatch(text):
                # Limit long suggestions at existing local word boundaries.
                if len(text)>8:result.extend(make_plan(text))
                else:result.append(Chunk(text,tuple(phonetics[start:end])))
            else:result.append(Chunk(text))
        validate_plan(source,result)
        return result
    except (ValueError,KeyError,TypeError,RuntimeError,OSError) as error:
        # A stopped session must propagate, not be treated as model fallback.
        from .engine import Stopped
        if isinstance(error,Stopped):raise
        progress('Qwen 未提供可用计划，已使用本地词典：'+str(error)[:100])
        return baseline
