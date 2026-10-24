import requests
import re

def _parse_gotquestions_html(html, qid):
    match = re.search(
        r'"question":\{"id":(\d+),"number":\d+,"text":"((?:[^"\\]|\\.)*)","razdatkaText":"(?:[^"\\]|\\.)*",'
        r'"razdatkaPic":"(?:[^"\\]|\\.)*","audio":"(?:[^"\\]|\\.)*","commentAudio":"(?:[^"\\]|\\.)*",'
        r'"answer":"((?:[^"\\]|\\.)*)","answerPic":"(?:[^"\\]|\\.)*","zachet":"(?:[^"\\]|\\.)*",'
        r'"nezachet":"(?:[^"\\]|\\.)*","comment":"((?:[^"\\]|\\.)*)"',
        html,
    )
    if not match:
        return None
    gq_id = match.group(1)
    question_text = match.group(2).encode('utf-8').decode('unicode_escape').strip()
    answer_text = match.group(3).encode('utf-8').decode('unicode_escape').strip()
    comment_text = match.group(4).encode('utf-8').decode('unicode_escape').strip() or None
    if not question_text or not answer_text:
        return None
    return {
        'question_id': f'gq-{gq_id}',
        'question_text': question_text,
        'answer_text': answer_text,
        'comment_text': comment_text,
    }

for qid in [5000]:
    resp = requests.get(f'https://gotquestions.online/question/{qid}', headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
    if resp.status_code != 200:
        print(f'Q{qid}: HTTP {resp.status_code}')
        continue
    q = _parse_gotquestions_html(resp.text, qid)
    if q:
        print(f'Q{qid}: id={q["question_id"]}, text={q["question_text"][:80]}..., answer={q["answer_text"][:50]}')
    else:
        # Debug: find the raw question pattern
        # Search for id:5000 pattern
        import re as _re
        m = _re.search(r'\\?"id\\?":\s*5000', resp.text)
        if m:
            start = max(0, m.start() - 50)
            print(f'Q{qid}: FOUND id at {m.start()}, context: {repr(resp.text[start:m.start()+400])}')
        else:
            print(f'Q{qid}: no id:5000 found, len={len(resp.text)}')






