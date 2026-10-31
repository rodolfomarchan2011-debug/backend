-- ============================================================
-- Clube Diana - Fase 2 (LGPD + consentimento + newsletter)
-- Rodar em cima do banco clube_diana (TiDB).
-- Idempotente quando aplicado por scripts/migrar_fase2.py
-- (o script confere antes de alterar).
-- ============================================================

-- 1) Consentimento com data e versao do texto aceito
ALTER TABLE clientes
  ADD COLUMN aceite_em DATETIME NULL AFTER aceite_lgpd,
  ADD COLUMN aceite_versao VARCHAR(20) NULL AFTER aceite_em;

-- 2) Exclusao de conta (anonimizacao, sem apagar historico fiscal)
ALTER TABLE clientes
  ADD COLUMN excluido_em DATETIME NULL AFTER last_synced_at;

-- 3) Novo estado de sincronizacao para conta excluida
ALTER TABLE clientes
  MODIFY COLUMN sync_status ENUM('pendente','sincronizado','erro','ignorado')
  NOT NULL DEFAULT 'pendente';

-- 4) Newsletter do site (antes ficava so no localStorage do navegador)
CREATE TABLE IF NOT EXISTS newsletter (
  id        CHAR(36)     NOT NULL,
  email     VARCHAR(190) NOT NULL,
  origem    VARCHAR(40)  NULL,
  criado_em DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_newsletter_email (email)
) ENGINE=InnoDB;
