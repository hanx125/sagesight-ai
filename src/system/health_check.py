"""
系统健康检查

- 任务子进程写就绪心跳（多阶段：started → detectors_ready → processors_ready → running）
- 主进程读取心跳判定任务是否真正就绪（进程活着但内部未就绪/卡死也能发现）
- 启动自检报告：启动末尾输出一次汇总日志
- /api/health：实时健康状态（供人工查看或外部看门狗轮询）
"""

import json
import os
import time
import logging
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# 心跳文件目录（与 ai_stream_flags 同级，均在 data/ 下）
HEARTBEAT_DIR = Path(__file__).resolve().parents[2] / 'data' / 'task_heartbeat'

# 心跳文件被视为过期的秒数（子进程每 10s 刷新一次）
HEARTBEAT_STALE_SECONDS = 60

# 合法阶段及顺序（index 越大越接近完全就绪）
STAGES = ['started', 'detectors_ready', 'processors_ready', 'running']


def _heartbeat_path(task_id) -> Path:
    return HEARTBEAT_DIR / f'{task_id}.json'


def write_heartbeat(task_id, pid: int, stage: str, extra: Optional[Dict] = None):
    """子进程写就绪心跳（原子写：先写临时文件再替换）"""
    try:
        HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            'task_id': task_id,
            'pid': pid,
            'stage': stage,
            'ts': time.time(),
        }
        if extra:
            payload.update(extra)
        path = _heartbeat_path(task_id)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        tmp.replace(path)
    except Exception as e:
        logger.debug(f"[HealthCheck] 写心跳失败 task={task_id}: {e}")


def read_heartbeat(task_id) -> Optional[Dict]:
    """读取任务心跳；文件不存在或损坏返回 None"""
    try:
        path = _heartbeat_path(task_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding='utf-8'))
        data['age'] = round(time.time() - data.get('ts', 0), 1)
        return data
    except Exception:
        return None


def clear_heartbeat(task_id):
    """清除任务心跳（启动任务前调用，避免读到上一次运行的心跳造成误判）"""
    try:
        path = _heartbeat_path(task_id)
        if path.exists():
            path.unlink()
    except Exception:
        pass


def heartbeat_is_ready(hb: Optional[Dict]) -> bool:
    """心跳是否达到完全就绪且新鲜"""
    if not hb:
        return False
    return hb.get('stage') == 'running' and hb.get('age', 9999) <= HEARTBEAT_STALE_SECONDS


def format_startup_report(report: Dict) -> str:
    """格式化启动自检报告为多行日志文本"""
    lines = ['', '=' * 60, '[启动自检报告]']
    for item in report.get('items', []):
        status = item.get('status', 'UNKNOWN')
        mark = 'OK  ' if status == 'ok' else ('FAIL' if status == 'fail' else 'WARN')
        lines.append(f"  [{mark}] {item.get('name', '')}: {item.get('detail', '')}")
    lines.append('=' * 60)
    return '\n'.join(lines)


def collect_task_health(pm, tm) -> Dict:
    """收集任务健康状态（/api/health 与启动自愈共用）

    Returns:
        {
          'tasks': [ {task_id, task_name, expected, process_alive, stage, heartbeat_age, healthy} ],
          'expected_count', 'healthy_count'
        }
    """
    tasks_health = []
    try:
        all_tasks = tm.get_all_tasks(enabled_only=True)
    except Exception as e:
        logger.warning(f"[HealthCheck] 读取任务列表失败: {e}")
        all_tasks = []

    expected_ids = set()
    for task in all_tasks:
        try:
            cfg = task.to_dict()
        except Exception:
            cfg = {'id': getattr(task, 'id', None), 'task_name': getattr(task, 'task_name', '?')}
        task_id = cfg.get('id')
        if cfg.get('status') != 'running':
            continue
        expected_ids.add(task_id)

        process = pm.task_processes.get(task_id)
        process_alive = bool(process and process.is_alive())
        hb = read_heartbeat(task_id)
        healthy = process_alive and heartbeat_is_ready(hb)
        tasks_health.append({
            'task_id': task_id,
            'task_name': cfg.get('task_name', '?'),
            'expected': True,
            'process_alive': process_alive,
            'stage': (hb or {}).get('stage', 'no_heartbeat'),
            'heartbeat_age': (hb or {}).get('age'),
            'healthy': healthy,
        })

    # 不在期望列表但仍持有进程记录的（如刚被停止，心跳尚未清）
    for task_id, process in pm.task_processes.items():
        if task_id in expected_ids:
            continue
        if process.is_alive():
            tasks_health.append({
                'task_id': task_id,
                'task_name': f'task-{task_id}',
                'expected': False,
                'process_alive': True,
                'stage': 'unexpected',
                'heartbeat_age': None,
                'healthy': False,
            })

    return {
        'tasks': tasks_health,
        'expected_count': len(expected_ids),
        'healthy_count': sum(1 for t in tasks_health if t['healthy']),
    }
