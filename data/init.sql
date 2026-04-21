-- init.sql - Script de inicialización de la base de datos PostgreSQL
-- Ejecutar: psql -U postgres -f init.sql

-- Crear base de datos
CREATE DATABASE control_biblioteca
    WITH ENCODING 'UTF8'
    LC_COLLATE = 'es_PE.UTF-8'
    LC_CTYPE = 'es_PE.UTF-8';

\c control_biblioteca;

-- Tabla de alumnos (caché local)
CREATE TABLE IF NOT EXISTS alumnos (
    id SERIAL PRIMARY KEY,
    codigo VARCHAR(20) UNIQUE NOT NULL,
    nombres VARCHAR(100) NOT NULL,
    apellidos VARCHAR(100) NOT NULL,
    escuela VARCHAR(100),
    habilitado BOOLEAN DEFAULT TRUE,
    ultima_sync TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_alumnos_codigo ON alumnos(codigo);

-- Tabla de terminales
CREATE TABLE IF NOT EXISTS terminales (
    id SERIAL PRIMARY KEY,
    nombre VARCHAR(50) UNIQUE NOT NULL,
    ip VARCHAR(45) NOT NULL,
    estado VARCHAR(20) DEFAULT 'bloqueado',
    ultima_conexion TIMESTAMP
);

-- Tabla de sesiones
CREATE TABLE IF NOT EXISTS sesiones (
    id SERIAL PRIMARY KEY,
    alumno_id INTEGER NOT NULL REFERENCES alumnos(id),
    terminal_id INTEGER NOT NULL REFERENCES terminales(id),
    inicio TIMESTAMP DEFAULT NOW(),
    fin TIMESTAMP,
    activa BOOLEAN DEFAULT TRUE,
    motivo_cierre VARCHAR(50)
);

CREATE INDEX idx_sesiones_activas ON sesiones(activa) WHERE activa = TRUE;

-- Tabla de usuarios administradores
CREATE TABLE IF NOT EXISTS usuarios (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    nombre_completo VARCHAR(150),
    rol VARCHAR(20) DEFAULT 'encargado',
    activo BOOLEAN DEFAULT TRUE
);

-- Datos de prueba: alumnos de ejemplo
INSERT INTO alumnos (codigo, nombres, apellidos, escuela, habilitado) VALUES
    ('2021001', 'Juan Carlos', 'Pérez López', 'Ing. de Sistemas', TRUE),
    ('2021002', 'María Elena', 'García Huamán', 'Ing. Civil', TRUE),
    ('2021003', 'Luis Alberto', 'Rodríguez Meza', 'Derecho', TRUE),
    ('2021004', 'Ana Patricia', 'Torres Vargas', 'Medicina', TRUE),
    ('2021005', 'Carlos Eduardo', 'Sánchez Ríos', 'Ing. Ambiental', FALSE)
ON CONFLICT (codigo) DO NOTHING;

-- Datos de prueba: terminales
INSERT INTO terminales (nombre, ip, estado) VALUES
    ('PC-01', '192.168.1.101', 'bloqueado'),
    ('PC-02', '192.168.1.102', 'bloqueado'),
    ('PC-03', '192.168.1.103', 'bloqueado'),
    ('PC-04', '192.168.1.104', 'bloqueado'),
    ('PC-05', '192.168.1.105', 'offline')
ON CONFLICT (nombre) DO NOTHING;
