-- ============================================================
-- Clube Diana - Fase 3
-- 1) descadastro da newsletter (LGPD)
-- 2) nada mais: os backups e o painel usam as tabelas existentes
-- Aplicado por scripts/migrar_fase3.py (confere antes de alterar).
-- ============================================================

ALTER TABLE newsletter
  ADD COLUMN token CHAR(32) NULL,
  ADD COLUMN descadastrado_em DATETIME NULL;

ALTER TABLE newsletter
  ADD UNIQUE KEY uq_newsletter_token (token);
