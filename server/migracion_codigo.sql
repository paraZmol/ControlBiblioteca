-- ============================================================
-- MIGRACIÓN 1: Corregir registros donde codigo = dni por error
-- ============================================================

-- Ver cuántos registros están afectados
SELECT COUNT(*) AS afectados FROM alumnos_maestro WHERE codigo = dni;

-- Limpiar: poner a NULL donde codigo era igual al DNI
UPDATE alumnos_maestro SET codigo = NULL WHERE codigo = dni;

-- Verificar
SELECT COUNT(*) AS sin_codigo FROM alumnos_maestro WHERE codigo IS NULL;


-- ============================================================
-- MIGRACIÓN 2: Quitar puntos de facultades y escuelas ya en BD
-- ============================================================

-- Ver facultades con punto
SELECT id, nombre FROM facultades WHERE nombre LIKE '%.%';

-- Quitar puntos de facultades
UPDATE facultades SET nombre = REPLACE(nombre, '.', '') WHERE nombre LIKE '%.%';

-- Ver escuelas con punto
SELECT id, nombre FROM escuelas WHERE nombre LIKE '%.%';

-- Quitar puntos de escuelas
UPDATE escuelas SET nombre = REPLACE(nombre, '.', '') WHERE nombre LIKE '%.%';

-- Eliminar duplicados que quedaron tras limpiar puntos
-- (ej: "FCM." y "FCM" serían ambos "FCM" → unificar)
-- Primero ver si hay duplicados:
SELECT nombre, COUNT(*) AS total FROM facultades GROUP BY nombre HAVING total > 1;
SELECT nombre, COUNT(*) AS total FROM escuelas   GROUP BY nombre HAVING total > 1;

-- ============================================================
-- DESPUÉS de ejecutar esto, re-importar el Excel del maestro
-- desde el panel Admin > Base de Datos > Importar Excel
-- El upsert por DNI actualizará los campos correctamente
-- ============================================================
