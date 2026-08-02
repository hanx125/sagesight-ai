/**
 * 配置字段安全加载工具
 * 用于检查HTML元素是否存在，避免null错误
 */

function safeSetValue(elementId, value) {
    const element = document.getElementById(elementId);
    if (element) {
        element.value = value;
    } else {
        console.warn(`[ConfigModal] 元素不存在: ${elementId}`);
    }
}

function safeGetValue(elementId, defaultValue) {
    const element = document.getElementById(elementId);
    if (element) {
        return element.value;
    } else {
        console.warn(`[ConfigModal] 元素不存在: ${elementId}`);
        return defaultValue;
    }
}

// 测试所有新增字段是否存在于DOM中
function testNewFields() {
    const newFields = [
        // event_config
        'eventGifEnabled',
    ];

    console.log('[ConfigModal] 测试新增字段...');

    let missing = [];
    let found = [];

    newFields.forEach(fieldId => {
        const element = document.getElementById(fieldId);
        if (element) {
            found.push(fieldId);
        } else {
            missing.push(fieldId);
        }
    });

    console.log(`[ConfigModal] 找到字段: ${found.length}/${newFields.length}`);

    if (missing.length > 0) {
        console.error('[ConfigModal] 缺失字段:', missing);
    } else {
        console.log('[ConfigModal] 所有字段都存在！');
    }

    return { found, missing };
}

// 自动测试（页面加载后）
if (typeof window !== 'undefined') {
    window.addEventListener('load', () => {
        setTimeout(() => {
            testNewFields();
        }, 1000);
    });
}