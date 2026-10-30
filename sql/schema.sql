-- ============================================================
-- Clube Diana - banco isolado (TiDB)
-- Banco: clube_diana
-- Regras: sem DELETE dos dados; sync seguro com o SIM.
-- ============================================================

CREATE DATABASE IF NOT EXISTS clube_diana
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE clube_diana;

-- ------------------------------------------------------------
-- Clientes do clube (cadastro do site)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS clientes (
  id              CHAR(36)      NOT NULL,
  nome            VARCHAR(160)  NOT NULL,
  cpf             CHAR(11)      NOT NULL,
  telefone        VARCHAR(20)   NULL,
  email           VARCHAR(160)  NULL,
  data_nascimento DATE          NULL,
  senha_hash      VARCHAR(255)  NOT NULL,
  aceite_lgpd     TINYINT(1)    NOT NULL DEFAULT 0,

  -- vinculo com o SIM (ERP local)
  sim_pessoa_id   CHAR(36)      NULL,
  sim_cliente_id  CHAR(36)      NULL,
  sync_status     ENUM('pendente','sincronizado','erro') NOT NULL DEFAULT 'pendente',
  sync_erro       TEXT          NULL,
  sync_tentativas INT           NOT NULL DEFAULT 0,
  last_synced_at  DATETIME      NULL,

  criado_em       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  atualizado_em   DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

  PRIMARY KEY (id),
  UNIQUE KEY uq_clientes_cpf (cpf),
  KEY idx_clientes_sync (sync_status, sync_tentativas)
) ENGINE=InnoDB;

-- ------------------------------------------------------------
-- Compras puxadas do SIM (somente de CPFs cadastrados no clube)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS compras (
  id                CHAR(36)      NOT NULL,
  cliente_id        CHAR(36)      NOT NULL,
  sim_venda_id      CHAR(36)      NOT NULL,
  sim_lancamento_id CHAR(36)      NULL,
  data_venda        DATETIME      NOT NULL,
  total_produtos    DECIMAL(12,2) NOT NULL DEFAULT 0,
  total_desconto    DECIMAL(12,2) NOT NULL DEFAULT 0,
  total_liquido     DECIMAL(12,2) NOT NULL DEFAULT 0,
  cupom             VARCHAR(60)   NULL,
  itens_json        JSON          NULL,
  synced_at         DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

  PRIMARY KEY (id),
  UNIQUE KEY uq_compras_venda (sim_venda_id),
  KEY idx_compras_cliente_data (cliente_id, data_venda)
) ENGINE=InnoDB;

-- ------------------------------------------------------------
-- Controle do agente de sincronizacao (watermark etc.)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sync_control (
  chave         VARCHAR(64)  NOT NULL,
  valor         VARCHAR(255) NULL,
  atualizado_em DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (chave)
) ENGINE=InnoDB;
