"""Chạy và chấm eval. expected chỉ cung cấp cho judge, không gửi cho agent."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import run_agent
from env_loader import load_env_file
from providers import Provider
from scenarios import ScenarioTools
from grading import grade_case, summarize, bonus_points
from bonus.tools import BonusTools


def _retry_on_429(fn, max_retries=5, base_delay=2.0, label=''):
    """Thực thi fn với retry exponential backoff khi gặp 429 hoặc timeout tạm thời."""
    last_exc = None
    for attempt in range(max_retries):
        try:
            return fn()
        except RuntimeError as exc:
            msg = str(exc)
            transient = 'HTTP 429' in msg or 'timed out' in msg.lower() or 'timeout' in msg.lower()
            if transient:
                last_exc = exc
                delay = base_delay * (2 ** attempt)
                print(f'  [{label}] lỗi tạm thời ({msg[:60]}), thử lại sau {delay:.0f}s (lần {attempt+1}/{max_retries})', flush=True)
                time.sleep(delay)
                continue
            raise
    raise last_exc


def run_case(case, args):
    label = case["id"]
    try:
        result = _retry_on_429(
            lambda: run_agent(case["question"], Provider(args.provider, args.model),
                              (BonusTools("snapshot", scenario=case.get("scenario", "normal")) if getattr(args, "bonus", False) else ScenarioTools(case.get("scenario", "normal"), case.get("fail", False))), max_steps=args.max_steps),
            label=label,
        )
    except Exception as exc:
        err_msg = type(exc).__name__ + ": " + str(exc)[:200]
        result = {"status": "error", "answer": "", "trace": [], "usage": [], "error": err_msg}
    if not isinstance(result, dict) or not isinstance(result.get("trace", []), list):
        result = {"status": "error", "answer": "", "trace": [], "usage": [], "error": "run_agent tra ket qua khong hop le."}
    try:
        judge = Provider(args.judge_provider, args.judge_model)
        grade = _retry_on_429(
            lambda: grade_case(case, result, judge),
            label=label,
        )
    except Exception as exc:
        err_msg = str(exc)
        grade = {"score": None, "status": "unscored", "reason": err_msg, "judge_usage": {}}
    return {'id': case['id'], 'question': case['question'], 'expected': case['expected'],
            'scenario': case.get('scenario', 'normal'), 'provider': args.provider, 'model': args.model,
            'judge_provider': args.judge_provider, 'judge_model': args.judge_model, 'result': result, 'grade': grade}

def main():
    load_env_file()
    parser = argparse.ArgumentParser(description='Chạy agent và chấm eval 0–100')
    choices = ['openai', 'gemini', 'anthropic', 'openrouter', 'opencode']
    parser.add_argument('--provider', choices=choices, default=os.getenv('PROVIDER', 'openai'))
    parser.add_argument('--model', default=os.getenv('MODEL'))
    parser.add_argument('--judge-provider', choices=choices, default=os.getenv('JUDGE_PROVIDER'))
    parser.add_argument('--judge-model', default=os.getenv('JUDGE_MODEL'))
    parser.add_argument('--bonus', action='store_true', help='Chạy bộ bonus riêng; không ảnh hưởng điểm chính')
    parser.add_argument('--case', help='Ví dụ E2 hoặc H1; bỏ trống để chạy toàn bộ')
    parser.add_argument('--max-steps', type=int, default=None, help='Giới hạn model calls mỗi case: mặc định 8, bonus 16')
    parser.add_argument('--workers', type=int, default=1, help='Số case chạy đồng thời (mặc định 1)')
    parser.add_argument('--output', default='eval_results.json')
    args = parser.parse_args()
    args.judge_provider = args.judge_provider or args.provider
    args.judge_model = args.judge_model or args.model
    if not args.model or not args.judge_model:
        parser.error('Thiếu --model hoặc MODEL')
    args.max_steps = args.max_steps if args.max_steps is not None else (16 if args.bonus else 8)
    if args.max_steps < 1 or not 1 <= args.workers <= 8:
        parser.error('--max-steps phải >= 1 và --workers phải từ 1 đến 8')
    cases_path = (Path(__file__).resolve().parents[1] / 'bonus' / 'cases.json') if args.bonus else (Path(__file__).parent / 'cases.json')
    cases = json.loads(cases_path.read_text(encoding='utf-8'))
    selected = [c for c in cases if not args.case or c['id'] == args.case.upper()]
    if not selected:
        parser.error('Không tìm thấy case')
    def run_and_print(case):
        print(f"Đang chạy {case['id']}", flush=True)
        item = run_case(case, args)
        print(f"{case['id']}: {item['grade'].get('score')} ({item['grade']['status']})", flush=True)
        return item
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(run_and_print, selected))
    report = {'summary': summarize(results), 'config': {'provider': args.provider, 'model': args.model,
              'judge_provider': args.judge_provider, 'judge_model': args.judge_model, 'max_steps': args.max_steps,
              'dataset': 'bonus' if args.bonus else 'core'}, 'cases': results}
    summary = report['summary']
    if args.bonus:
        full_run = not args.case and len(selected) == len(cases)
        report['summary']['bonus_points_0_15'] = bonus_points(summary['score_0_100'], full_run)
        print(f"Điểm bonus tham khảo: {summary['bonus_points_0_15']}/15 (chỉ tính khi chấm đủ toàn bộ case).")
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"Điểm: {summary['score_0_100']}/100; đã chấm {summary['scored_cases']}/{summary['total_cases']} case. File: {args.output}")
    if summary['score_0_100'] is None:
        print('Chưa có điểm tổng do có case chưa được chấm; xem reason trong file.', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    sys.exit(main())
