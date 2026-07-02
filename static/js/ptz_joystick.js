// ========================================
// PTZ云台方向按钮控制
// ========================================

/**
 * 初始化PTZ方向箭头按钮事件
 */
function initPtzArrowButtons() {
    // 为所有箭头按钮添加点击事件
    const arrows = document.querySelectorAll('.ptz-arrow');
    arrows.forEach(arrow => {
        // 鼠标按下开始移动
        arrow.addEventListener('mousedown', function(e) {
            e.preventDefault();
            const direction = this.getAttribute('data-direction');
            if (direction && currentSingleCamera) {
                ptzControl(direction);
                ptzMoving = true;
            }
        });

        // 鼠标释放停止移动
        arrow.addEventListener('mouseup', function(e) {
            e.preventDefault();
            if (ptzMoving && currentSingleCamera) {
                ptzStop();
                ptzMoving = false;
            }
        });

        // 鼠标离开箭头区域停止移动
        arrow.addEventListener('mouseleave', function(e) {
            if (ptzMoving && currentSingleCamera) {
                ptzStop();
                ptzMoving = false;
            }
        });

        // 触摸屏支持
        arrow.addEventListener('touchstart', function(e) {
            e.preventDefault();
            const direction = this.getAttribute('data-direction');
            if (direction && currentSingleCamera) {
                ptzControl(direction);
                ptzMoving = true;
            }
        });

        arrow.addEventListener('touchend', function(e) {
            e.preventDefault();
            if (ptzMoving && currentSingleCamera) {
                ptzStop();
                ptzMoving = false;
            }
        });
    });

    console.log('✓ PTZ方向箭头按钮已初始化');
}