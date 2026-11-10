-- B 站直播监听数据表（与 bili_live_monitor 的表结构兼容）

CREATE TABLE IF NOT EXISTS `danmu_messages` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `room_id` bigint(20) unsigned NOT NULL,
  `user_id` bigint(20) unsigned NOT NULL,
  `username` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL,
  `content` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
  `medal_level` int(11) DEFAULT NULL,
  `medal_name` varchar(50) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `user_level` int(11) DEFAULT NULL,
  `timestamp` datetime DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_danmu_room_time` (`room_id`, `timestamp`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `gift_records` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `room_id` bigint(20) unsigned NOT NULL,
  `user_id` bigint(20) unsigned NOT NULL,
  `username` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL,
  `gift_name` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `gift_num` int(11) NOT NULL,
  `gift_price` decimal(10,2) NOT NULL,
  `total_price` decimal(10,2) NOT NULL,
  `timestamp` datetime DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `receiver_id` bigint(20) DEFAULT NULL,
  `receiver_name` varchar(50) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_gift_room_time` (`room_id`, `timestamp`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `guard_records` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `room_id` bigint(20) NOT NULL COMMENT '直播间ID',
  `user_id` bigint(20) NOT NULL COMMENT '用户UID',
  `username` varchar(255) NOT NULL COMMENT '用户名',
  `guard_type` tinyint(4) NOT NULL COMMENT '舰队类型(1=总督,2=提督,3=舰长)',
  `guard_name` varchar(50) NOT NULL COMMENT '舰队名称',
  `guard_num` int(11) NOT NULL COMMENT '购买数量',
  `price` decimal(10,2) NOT NULL COMMENT '单价(元)',
  `total_price` decimal(10,2) NOT NULL COMMENT '总价(元)',
  `start_time` datetime NOT NULL COMMENT '舰队开始时间',
  `end_time` datetime NOT NULL COMMENT '舰队结束时间',
  `timestamp` datetime NOT NULL COMMENT '购买时间',
  PRIMARY KEY (`id`),
  KEY `idx_room` (`room_id`),
  KEY `idx_user` (`user_id`),
  KEY `idx_time` (`timestamp`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='大航海购买记录表';

CREATE TABLE IF NOT EXISTS `entry_user_records` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `room_id` bigint(20) unsigned NOT NULL,
  `user_id` bigint(20) unsigned NOT NULL,
  `username` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `entry_content` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_entry_room` (`room_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `super_chat_records` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `room_id` bigint(20) unsigned NOT NULL,
  `user_id` bigint(20) unsigned NOT NULL,
  `username` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL,
  `content` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
  `price` decimal(10,2) NOT NULL COMMENT 'SC 金额(元)',
  `gift_name` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `start_time` datetime NOT NULL,
  `end_time` datetime NOT NULL,
  `timestamp` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_sc_room_time` (`room_id`, `timestamp`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='醒目留言记录表';
