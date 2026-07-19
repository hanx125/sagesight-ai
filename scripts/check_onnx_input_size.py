"""
检查 ONNX 模型的输入尺寸是否支持动态调整
"""
import sys
import onnx
from pathlib import Path


def check_onnx_input_size(model_path: str):
    """检查 ONNX 模型输入尺寸"""
    print(f"\n检查模型: {model_path}")
    print("=" * 60)

    # 加载模型
    model = onnx.load(model_path)

    # 获取输入信息
    input_info = model.graph.input[0]
    input_name = input_info.name

    print(f"输入名称: {input_name}")

    # 检查输入维度
    dims = []
    is_dynamic = False

    for i, dim in enumerate(input_info.type.tensor_type.shape.dim):
        if dim.dim_param:
            # 动态维度（有名称参数）
            dims.append(f"{dim.dim_param}(动态)")
            is_dynamic = True
        else:
            # 固定维度
            dims.append(str(dim.dim_value))

    print(f"输入形状: [{', '.join(dims)}]")

    # 判断是否支持动态输入
    if is_dynamic:
        print("\n✅ 模型支持动态输入尺寸")
        print("   可以在配置中设置 32 的倍数的输入尺寸（如 320, 352, 384, ...）")
    else:
        print("\n❌ 模型输入尺寸固定")
        print(f"   必须使用固定的输入尺寸: {dims[2]}x{dims[3]}")
        print("   如需支持其他尺寸，需要重新导出模型")

    # 检查所有输入
    print(f"\n模型总输入数: {len(model.graph.input)}")
    for i, inp in enumerate(model.graph.input):
        shape = []
        for dim in inp.type.tensor_type.shape.dim:
            if dim.dim_param:
                shape.append(f"{dim.dim_param}(动态)")
            else:
                shape.append(str(dim.dim_value))
        print(f"  输入 {i}: {inp.name} [{', '.join(shape)}]")

    return is_dynamic


def main():
    # 检查所有模型文件
    models_dir = Path("models")
    if not models_dir.exists():
        print("错误: models 目录不存在")
        return

    onnx_files = list(models_dir.glob("**/*.onnx"))

    if not onnx_files:
        print("未找到 ONNX 模型文件")
        return

    print(f"找到 {len(onnx_files)} 个 ONNX 模型文件")

    for model_file in onnx_files:
        try:
            check_onnx_input_size(str(model_file))
        except Exception as e:
            print(f"\n检查失败: {e}")


if __name__ == "__main__":
    main()