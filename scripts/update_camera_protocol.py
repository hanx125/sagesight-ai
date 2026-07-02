"""
更新摄像机protocol字段

手动更新现有摄像机的protocol字段：
- 有ptz_address的摄像机 → protocol='onvif'
- 无ptz_address的摄像机 → protocol='rtsp'（默认值）
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def update_camera_protocol():
    """更新摄像机protocol字段"""
    db_path = "data/tasks.db"
    
    if not os.path.exists(db_path):
        logger.error(f"数据库文件不存在: {db_path}")
        return
    
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            
            # 检查protocol字段是否存在
            cursor.execute("PRAGMA table_info(cameras)")
            columns = [row[1] for row in cursor.fetchall()]
            
            if 'protocol' not in columns:
                logger.info("protocol字段不存在，添加字段...")
                cursor.execute("ALTER TABLE cameras ADD COLUMN protocol TEXT DEFAULT 'rtsp'")
                conn.commit()
                logger.info("protocol字段添加成功")
            
            # 统计当前摄像机数量
            cursor.execute("SELECT COUNT(*) FROM cameras")
            total_count = cursor.fetchone()[0]
            logger.info(f"当前摄像机总数: {total_count}")
            
            # 统计ONVIF摄像机数量（有ptz_address）
            cursor.execute("SELECT COUNT(*) FROM cameras WHERE ptz_address != '' AND ptz_address IS NOT NULL")
            onvif_count = cursor.fetchone()[0]
            logger.info(f"ONVIF摄像机数量（有ptz_address）: {onvif_count}")
            
            # 统计RTSP摄像机数量（无ptz_address）
            cursor.execute("SELECT COUNT(*) FROM cameras WHERE ptz_address = '' OR ptz_address IS NULL")
            rtsp_count = cursor.fetchone()[0]
            logger.info(f"RTSP摄像机数量（无ptz_address）: {rtsp_count}")
            
            # 更新ONVIF摄像机的protocol字段
            cursor.execute("UPDATE cameras SET protocol = 'onvif' WHERE ptz_address != '' AND ptz_address IS NOT NULL")
            onvif_updated = cursor.rowcount
            logger.info(f"更新ONVIF摄像机protocol字段: {onvif_updated}条")
            
            # 更新RTSP摄像机的protocol字段（确保默认值）
            cursor.execute("UPDATE cameras SET protocol = 'rtsp' WHERE ptz_address = '' OR ptz_address IS NULL AND (protocol IS NULL OR protocol = '')")
            rtsp_updated = cursor.rowcount
            logger.info(f"更新RTSP摄像机protocol字段: {rtsp_updated}条")
            
            conn.commit()
            logger.info("数据库更新完成！")
            
            # 显示更新后的统计
            cursor.execute("SELECT COUNT(*) FROM cameras WHERE protocol = 'onvif'")
            final_onvif = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM cameras WHERE protocol = 'rtsp'")
            final_rtsp = cursor.fetchone()[0]
            logger.info(f"最终统计: ONVIF={final_onvif}, RTSP={final_rtsp}")
            
    except Exception as e:
        logger.error(f"更新失败: {e}")
        raise

if __name__ == "__main__":
    logger.info("开始更新摄像机protocol字段...")
    update_camera_protocol()
    logger.info("更新完成！")