#!/usr/bin/env python3
import os
import re
import sys
import requests
from dotenv import load_dotenv


# ── ANSI / control-code stripping ────────────────────────────────────────────
# Covers CSI (color, cursor), OSC (title, hyperlinks), and bare ESC sequences.
ANSI_RE = re.compile(
    r'\x1b(?:'
    r'[@-Z\\^_]'                        
    r'|\[[0-?]*[ -/]*[@-~]'             
    r'|\][^\x07\x1b]*(?:\x07|\x1b\\)'   
    r'|[PX^_][^\x1b]*\x1b\\'            
    r')'
)

def strip_ansi(raw: bytes) -> str:
    text = raw.decode('utf-8', errors='replace')
    text = ANSI_RE.sub('', text)
    #strip ascii bell
    text = text.replace('\x07', '')
    # Simulate carriage-return overwriting (progress bars, etc.)
    # lines = [seg.split('\r')[-1] for seg in text.split('\n')]
    lines = []
    for line in text.split('\n'):
        parts = line.split('\r')
        buf = []
        for part in parts:
            if not part:
                continue
            # Overwrite the buffer from the start with the new segment's characters
            buf[0:len(part)] = list(part)
        lines.append(''.join(buf))
    return '\n'.join(lines)


# ── Session log extraction ────────────────────────────────────────────────────
def last_block() -> tuple[str, str]:
    log_file   = os.environ.get('EXPLAIN_LOG', '')
    marks_file = os.environ.get('EXPLAIN_MARKS', '')

    if not log_file or not marks_file:
        sys.exit(
            '❌ EXPLAIN_LOG / EXPLAIN_MARKS not set.\n'
            '   Paste the explain block at the top of ~/.zshrc and open a new shell.'
        )

    try:
        marks = open(marks_file).read().strip().splitlines()
    except FileNotFoundError:
        sys.exit('❌ No marks file yet — run a command first, then `explain`.')

    # Walk backwards to find the last complete START → END pair.
    end_ln = cmd = start_ln = None
    for entry in reversed(marks):
        parts = entry.split('\t', 2)
        if parts[0] == 'END' and end_ln is None:
            end_ln = int(parts[1])
        elif parts[0] == 'START' and end_ln is not None and start_ln is None:
            start_ln = int(parts[1])
            cmd = parts[2] if len(parts) > 2 else ''
            break

    if start_ln is None:
        sys.exit('❌ No completed command found. Run a command first, then `explain`.')

    with open(log_file, 'rb') as f:
        raw_lines = f.read().split(b'\n')
    # print(f"raw_lines is {raw_lines}")
    output = strip_ansi(b'\n'.join(raw_lines[start_ln:end_ln])).strip()
    # print(f"output is {output}")
    return cmd, output


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    load_dotenv()

    api_key  = os.environ.get('GEMINI_API_KEY')
    model_id = os.environ.get('MODEL_ID', 'gemini-2.5-flash')

    if not api_key:
        sys.exit('❌ GEMINI_API_KEY is not set.')

    cmd, output = last_block()
    # print(f"output in main is {output}")
    screen_text = f'COMMAND: {cmd}\nOUTPUT: {output}' if output else f'COMMAND: {cmd}'

    sys.stdin = open('/dev/tty')   # reconnect stdin for the interactive prompt
    confirm = input(
        f'\n🤖 Send to {model_id}:\n'
        + '-' * 40
        + f'\n{screen_text}\n'
        + '-' * 40
        + '\n[y to send] > '
    )
    if confirm.strip() != 'y':
        sys.exit(0)

    url     = 'https://generativelanguage.googleapis.com/v1beta/interactions'
    headers = {'x-goog-api-key': api_key}

    schema = {
    'type': 'object',
    'properties': {
        'success_explanation': {
            'type': 'string',
            'description': 'If the OUTPUT is successful OR not present, Provide brief explanation of what the command did'
        },
        'error_details': {
            'type': 'object',
            'description': 'ONLY If the OUTPUT is an error, provide these error details.',
            'properties': {
                'explanation': {
                    'type': 'string',   
                    'description': 'Provide brief explanation of the cause of error.'
                },
                'has_fix': {
                    'type': 'boolean',
                    'description': 'True if a corrective command or workaround exists to resolve the error, false otherwise.'
                },
                'fix': {
                    'type': 'string',
                    'description': 'The proposed command fix or resolution steps. Only populate this field if has_fix is true.'
                }
            },
            'required': ['explanation', 'has_fix']
        }
    }
    }

    payload = {
        'model': model_id,
        'input': f'{screen_text}',
        'response_format': {
            'type': 'text',
            'mime_type': 'application/json',
            'schema': schema
        }
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data      = response.json()
        steps     = data.get('steps', [])
        model_out = next((s for s in steps if s.get('type') == 'model_output'), None)
        if not model_out:
            print('❌ No model_output in response.')
            return
        text_item = next(
            (c for c in model_out.get('content', []) if c.get('type') == 'text'), None
        )
        if not text_item:
            print('❌ No text content in model_output.')
            return
        print(text_item['text'])

    except requests.exceptions.HTTPError as e:
        print(f'❌ API Error: {e}\nDetails: {response.text}')
    except requests.exceptions.RequestException as e:
        print(f'❌ Network Error: {e}')


if __name__ == '__main__':
    main()