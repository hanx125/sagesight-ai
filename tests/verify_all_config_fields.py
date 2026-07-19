"""
验证所有配置字段是否存在
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
TASKS_HTML = PROJECT_ROOT / 'templates' / 'tasks.html'
CONFIG_MODAL_JS = PROJECT_ROOT / 'static' / 'js' / 'config_modal.js'

# 所有字段列表（包括原有字段和新增字段）
ALL_FIELDS = [
    # decoder_config（原有）
    'decoderWidth',
    'decoderHeight',
    'decoderFps',
    'decoderHwaccelPreset',
    'decoderInputPreset',
    'decoderOutputFormat',
    'decoderRtspTransport',
    'decoderReconnect',
    'decoderReconnectDelay',
    'decoderBufferSize',
    'decoderRingBufferBlockCount',
    'decoderIndexQueueSize',
    'decoderFfmpegFilters',

    # detector_config（原有+新增）
    'detectorBackend',
    'detectorUseFp16',
    'detectorConfThreshold',
    'detectorIouThreshold',
    'detectorMinBoxArea',
    'detectorDeviceId',
    'detectorGpuMemLimit',
    'detectorPreprocessorWidth',
    'detectorPreprocessorHeight',
    'detectorPreprocessorModelInputSize',
    'detectorPreprocessorRgbMean',
    'detectorPreprocessorRgbStd',

    # tracker_config（原有）
    'trackerTrackThresh',
    'trackerMatchThresh',
    'trackerMaxAge',
    'trackerNInit',
    'trackerMaxHistoryLength',

    # motion_config（原有+新增）
    'motionHistory',
    'motionVarThreshold',
    'motionMinArea',
    'motionConfidenceBoost',
    'motionConfidencePenalty',
    'motionDetectShadows',
    'motionBlurSize',
    'motionMorphSize',
    'motionLearningRate',
    'motionHistoryLength',
    'motionInitThreshold',
    'motionForegroundThreshold',
    'motionMaxMotionRegions',
    'motionShowVisualization',
    'motionUseForReid',

    # event_config（原有+新增）
    'eventEnabled',
    'eventGifEnabled',
    'eventImageMethod',
    'eventLocalBackup',
    'eventImageOutputDir',
    'eventImageDrawBbox',
    'eventMinConfidence',
    'eventGifOutputMethod',
    'eventGifOutputDir',
    'eventGifDrawBbox',
    'eventCropMarginRatio',
    'eventGifCropMode',
    'eventGifPreSeconds',
    'eventGifPostSeconds',

    # display_config（原有）
    'displayEnabled',
    'displayTargetFps',
    'displayAIStreamEnabled',
    'displayAIStreamInterval',
    'displayShowBoxes',
    'displayShowTracks',
    'displayShowTrajectories',
    'displayShowMotionRegions',
    'displayTrajectoryHistory',
    'displayBoxColor',
    'displayBoxThickness',
    'displayBoxFontScale',
    'displayBoxLabelColor',
    'displayTrackColor',
    'displayTrackThickness',
    'displayTrackFontScale',
    'displayTrackLabelColor',
    'displayTrajectoryColor',
    'displayTrajectoryThickness',
    'displayTrajectoryPointSize',
    'displayTrajectoryPointColor',

    # display_config.debug_info（新增）
    'debugShowFps',
    'debugShowDetectionCount',
    'debugShowTrackCount',
    'debugShowFrameId',
    'debugFpsPosition',
    'debugFontScale',
    'debugTextColor',
]

def test_all_fields():
    print("\n" + "="*70)
    print("全面验证：所有配置字段")
    print("="*70)

    html = TASKS_HTML.read_text(encoding='utf-8')
    js = CONFIG_MODAL_JS.read_text(encoding='utf-8')

    missing_html = []
    missing_js = []
    found_html = []
    found_js = []

    for field in ALL_FIELDS:
        # HTML检查
        if f'id="{field}"' in html:
            found_html.append(field)
        else:
            missing_html.append(field)

        # JS检查
        if field in js:
            found_js.append(field)
        else:
            missing_js.append(field)

    print(f"\n总字段数: {len(ALL_FIELDS)}")
    print(f"HTML表单: 找到 {len(found_html)} 个字段")
    print(f"JS代码: 找到 {len(found_js)} 个字段")

    if missing_html:
        print(f"\n[FAIL] HTML缺失字段 ({len(missing_html)} 个):")
        for field in missing_html:
            print(f"  - {field}")

    if missing_js:
        print(f"\n[FAIL] JS缺失字段 ({len(missing_js)} 个):")
        for field in missing_js:
            print(f"  - {field}")

    if not missing_html and not missing_js:
        print("\n[OK] 所有字段已正确添加！")
        return True
    else:
        print(f"\n[FAIL] 部分字段缺失！")
        return False

if __name__ == "__main__":
    import sys
    sys.exit(0 if test_all_fields() else 1)