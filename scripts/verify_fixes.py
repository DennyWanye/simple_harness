#!/usr/bin/env python3
"""
简化测试：验证错误处理和状态恢复修复
通过检查代码和单元测试来验证修复的正确性
"""
import sys
import os

def test_health_check_timeout():
    """测试 #1: 验证健康检查超时已正确设置"""
    print("\n" + "="*60)
    print("🧪 测试 #1: 验证健康检查超时配置")
    print("="*60)

    controlws_path = "/Users/denny/projects/simple_harness/tauri-app/src/code-panel/controlWs.ts"

    if not os.path.exists(controlws_path):
        print(f"❌ 文件不存在: {controlws_path}")
        return False

    with open(controlws_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 检查超时值
    check_interval = "SESSION_HEALTH_CHECK_INTERVAL = 15_000" in content or "SESSION_HEALTH_CHECK_INTERVAL = 15000" in content
    check_timeout = "SESSION_STUCK_TIMEOUT = 60_000" in content or "SESSION_STUCK_TIMEOUT = 60000" in content

    print(f"检查健康检查间隔 (15秒): {'✅ 通过' if check_interval else '❌ 失败'}")
    print(f"检查卡住超时 (60秒): {'✅ 通过' if check_timeout else '❌ 失败'}")

    if check_interval and check_timeout:
        print("\n✅ 测试 #1 通过: 健康检查超时配置正确")
        return True
    else:
        print("\n❌ 测试 #1 失败: 健康检查超时配置不正确")
        return False


def test_error_handling_in_presenter():
    """测试 #2: 验证 run_presenter.py 中的错误处理"""
    print("\n" + "="*60)
    print("🧪 测试 #2: 验证表现层错误处理")
    print("="*60)

    presenter_path = "/Users/denny/projects/simple_harness/backend/deskpet/agent/run_presenter.py"

    if not os.path.exists(presenter_path):
        print(f"❌ 文件不存在: {presenter_path}")
        return False

    with open(presenter_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 检查关键修复
    checks = {
        "final_delivery_failed_CRITICAL": "run_presenter_final_delivery_failed_CRITICAL" in content,
        "error_delivery_failed_CRITICAL": "run_presenter_error_delivery_failed_CRITICAL" in content,
        "fallback_error_in_final": "chat_v2_error" in content and "final_delivery_failed" in content,
        "fallback_error_in_error": "websocket.send_json" in content and "Last resort" in content,
        "exc_info_logging": "exc_info=True" in content,
    }

    print("\n检查项:")
    for check_name, passed in checks.items():
        print(f"  {check_name}: {'✅' if passed else '❌'}")

    all_passed = all(checks.values())

    if all_passed:
        print("\n✅ 测试 #2 通过: 错误处理代码已正确添加")
        return True
    else:
        failed = [k for k, v in checks.items() if not v]
        print(f"\n❌ 测试 #2 失败: 以下检查未通过: {', '.join(failed)}")
        return False


def test_unit_tests_exist():
    """测试 #3: 验证单元测试存在且可运行"""
    print("\n" + "="*60)
    print("🧪 测试 #3: 验证单元测试")
    print("="*60)

    test_file = "/Users/denny/projects/simple_harness/backend/tests/test_error_handling_fixes.py"

    if not os.path.exists(test_file):
        print(f"❌ 测试文件不存在: {test_file}")
        return False

    print(f"✅ 测试文件存在: {test_file}")

    # 运行单元测试
    import subprocess
    try:
        result = subprocess.run(
            ["backend/.venv/bin/python", "-m", "pytest", test_file, "-v"],
            cwd="/Users/denny/projects/simple_harness",
            capture_output=True,
            text=True,
            timeout=30
        )

        print("\n单元测试输出:")
        print(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)

        if result.returncode == 0:
            print("\n✅ 测试 #3 通过: 所有单元测试通过")
            return True
        else:
            print(f"\n❌ 测试 #3 失败: 单元测试失败 (exit code: {result.returncode})")
            if result.stderr:
                print("错误输出:")
                print(result.stderr[-500:] if len(result.stderr) > 500 else result.stderr)
            return False

    except subprocess.TimeoutExpired:
        print("\n❌ 测试 #3 失败: 单元测试超时")
        return False
    except Exception as e:
        print(f"\n❌ 测试 #3 失败: {e}")
        return False


def test_documentation_complete():
    """测试 #4: 验证文档完整性"""
    print("\n" + "="*60)
    print("🧪 测试 #4: 验证文档完整性")
    print("="*60)

    docs = [
        ("/Users/denny/projects/simple_harness/FIX_SUMMARY_2026-08-17.md", "修复总结"),
        ("/Users/denny/projects/simple_harness/AGENT_LOOP_ERROR_HANDLING_FIX.md", "技术分析"),
        ("/Users/denny/projects/simple_harness/STATUS_UPDATE_2026-08-17.md", "状态更新"),
    ]

    all_exist = True
    for doc_path, doc_name in docs:
        exists = os.path.exists(doc_path)
        print(f"  {doc_name}: {'✅ 存在' if exists else '❌ 缺失'}")
        if not exists:
            all_exist = False

    if all_exist:
        print("\n✅ 测试 #4 通过: 所有文档齐全")
        return True
    else:
        print("\n❌ 测试 #4 失败: 部分文档缺失")
        return False


def main():
    """运行所有验证测试"""
    print("\n" + "="*60)
    print("🚀 开始验证修复完整性")
    print("="*60)
    print("目标: 验证状态恢复修复的代码和文档是否完整")

    results = {
        "health_check_timeout": test_health_check_timeout(),
        "error_handling": test_error_handling_in_presenter(),
        "unit_tests": test_unit_tests_exist(),
        "documentation": test_documentation_complete(),
    }

    # 汇总
    print("\n" + "="*60)
    print("📊 测试汇总")
    print("="*60)

    total = len(results)
    passed = sum(1 for r in results.values() if r)

    for test_name, result in results.items():
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{test_name}: {status}")

    print("-"*60)
    print(f"总计: {passed}/{total} 通过 ({passed*100//total}%)")

    if passed == total:
        print("\n🎉 所有验证测试通过！")
        print("✅ 修复代码完整")
        print("✅ 单元测试通过")
        print("✅ 文档齐全")
        print("\n📋 建议: 现在可以进行手动 UI 测试来验证实际效果")
        return True
    else:
        print(f"\n⚠️ {total - passed} 个测试失败")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
