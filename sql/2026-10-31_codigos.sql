-- ============================================================
-- Clube Diana - Fase 3: recuperacao de senha por codigo no WhatsApp
-- Aplicado por scripts/migrar_fase3_codigos.py
-- ============================================================

CREATE TABLE IF NOT EXISTS recuperacao (
  id            CHAR(36)     NOT NULL,
  cliente_id    CHAR(36)     NOT NULL,
  codigo_hash   VARCHAR(255) NOT NULL,
  telefone      VARCHAR(20)  NULL,
  expira_em     DATETIME     NOT NULL,
  tentativas    INT          NOT NULL DEFAULT 0,
  usado_em      DATETIME     NULL,
  enviado_em    DATETIME     NULL,
  erro_envio    VARCHAR(200) NULL,
  criado_em     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_rec_cliente (cliente_id, criado_em),
  KEY idx_rec_fila (enviado_em, expira_em)
) ENGINE=InnoDB;
