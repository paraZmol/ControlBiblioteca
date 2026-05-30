-- migracion_actividad_logs.sql
-- Ejecutar una sola vez en producción
-- Crea la tabla de log de actividad de alumnos en las PCs

USE biblioteca_unasam;

CREATE TABLE IF NOT EXISTS actividad_logs (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    id_terminal     INT NOT NULL,
    nombre_terminal VARCHAR(100) NOT NULL,
    dni_alumno      VARCHAR(8) NOT NULL,
    nombre_alumno   VARCHAR(200) NOT NULL,
    tipo            VARCHAR(30) NOT NULL COMMENT 'proceso | archivo | comando | navegador',
    descripcion     VARCHAR(300) NOT NULL,
    detalle         VARCHAR(600) NULL,
    nivel           VARCHAR(20) NOT NULL DEFAULT 'normal' COMMENT 'normal | sospechoso',
    fecha_hora      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_act_terminal  (id_terminal),
    INDEX idx_act_alumno    (dni_alumno),
    INDEX idx_act_fecha     (fecha_hora),
    INDEX idx_act_nivel     (nivel),

    CONSTRAINT fk_act_terminal FOREIGN KEY (id_terminal)
        REFERENCES terminales(id) ON DELETE CASCADE,
    CONSTRAINT fk_act_alumno FOREIGN KEY (dni_alumno)
        REFERENCES alumnos_maestro(dni) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
