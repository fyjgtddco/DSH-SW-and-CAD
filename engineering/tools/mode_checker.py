#!/usr/bin/env python3
"""
Mode Checker Tool - Mandatory Entry Point

This tool MUST be called before ANY design work begins.
It enforces the mode selection rule and logs the execution path.

Usage:
  python mode_checker.py <mode> [--verify]

Modes:
  1 - Basic Part (no subagent)
  2 - Complex Assembly (must use subagent)
  3 - Image Analysis (no subagent)
"""

import sys
import json
import os
from datetime import datetime

MODE_MAP = {
    '1': 'basic_part',
    '2': 'complex_assembly', 
    '3': 'image_analysis',
    'basic_part': '1',
    'complex_assembly': '2',
    'image_analysis': '3'
}

REQUIRES_SUBAGENT = {'2', 'complex_assembly'}

def check_mode(mode: str) -> dict:
    """Validate mode selection and return execution config."""
    mode_id = MODE_MAP.get(mode.lower(), mode)
    
    if mode_id not in MODE_MAP.values():
        return {
            'success': False,
            'error': f'Invalid mode: {mode}. Must be 1, 2, or 3'
        }
    
    requires_subagent = mode_id in REQUIRES_SUBAGENT
    
    return {
        'success': True,
        'mode': mode_id,
        'mode_name': mode_id,
        'requires_subagent': requires_subagent,
        'architecture': 'subagent_rooms' if requires_subagent else 'single_thread',
        'timestamp': datetime.now().isoformat()
    }

def main():
    if len(sys.argv) < 2:
        print(json.dumps({
            'success': False,
            'error': 'Usage: mode_checker.py <mode>',
            'modes': {'1': 'Basic Part', '2': 'Complex Assembly', '3': 'Image Analysis'}
        }, indent=2))
        sys.exit(1)
    
    mode = sys.argv[1]
    result = check_mode(mode)
    
    # Log to file for audit trail
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mode_logs')
    os.makedirs(log_dir, exist_ok=True)
    
    log_file = os.path.join(log_dir, f'{result["timestamp"]}_{mode}.json')
    with open(log_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    
    print(json.dumps(result, indent=2, ensure_ascii=False))

if __name__ == '__main__':
    main()
