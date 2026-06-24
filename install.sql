CREATE TABLE IF NOT EXISTS `endpoints-output-yealink-push` (
  `ipv4` VARCHAR(45) NOT NULL,
  `name` VARCHAR(255) NOT NULL DEFAULT '',
  `status` ENUM('New', 'Unchecked', 'Offline', 'Online') NOT NULL DEFAULT 'Unchecked',
  `username` VARCHAR(255) NOT NULL DEFAULT '',
  `password` VARCHAR(255) NOT NULL DEFAULT '',
  PRIMARY KEY (`ipv4`),
  KEY `status_idx` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
