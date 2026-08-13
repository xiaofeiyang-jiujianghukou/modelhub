"""
计费系统单元测试
覆盖：定价计算、余额预检、原子扣费、交易记录
"""

import pytest

from src.middleware.billing import (
    BillingService, calc_image_cost, calc_llm_cost, calc_video_cost,
)
from src.models import Balance, ModelCatalog, Transaction


# ── 定价计算（纯函数）──────────────────────────────────────────────────────────

class TestPricing:
    def test_llm_cost_calculation(self):
        """LLM 按 token 计费：输入2$/1M + 输出8$/1M"""
        model = ModelCatalog(id="m", model_type="llm", input_price=2.0, output_price=8.0)
        cost = calc_llm_cost(model, prompt_tokens=1000, completion_tokens=500)
        # (1000/1M)*2 + (500/1M)*8 = 0.002 + 0.004 = 0.006
        assert cost == pytest.approx(0.006, abs=1e-8)

    def test_llm_cost_zero_tokens(self):
        """零 token 不产生费用"""
        model = ModelCatalog(id="m", model_type="llm", input_price=2.0, output_price=8.0)
        assert calc_llm_cost(model, 0, 0) == 0

    def test_image_cost_per_image(self):
        """图像按张计费：n × unit_price"""
        model = ModelCatalog(id="m", model_type="image", unit_price=0.04)
        assert calc_image_cost(model, 1) == 0.04
        assert calc_image_cost(model, 4) == 0.16

    def test_video_cost_per_second(self):
        """视频按秒计费"""
        model = ModelCatalog(id="m", model_type="video", unit_price=0.5)
        assert calc_video_cost(model, 5) == 2.5


# ── 余额预检 ───────────────────────────────────────────────────────────────────

class TestPrecheck:
    @pytest.mark.asyncio
    async def test_precheck_sufficient(self, db_session, seed_user):
        """余额充足时预检通过"""
        balance = await BillingService.precheck_balance(db_session, seed_user.id)
        assert balance == pytest.approx(100.0)

    @pytest.mark.asyncio
    async def test_precheck_insufficient(self, db_session, seed_user):
        """余额不足时抛出 402"""
        from fastapi import HTTPException
        # 先把余额改成 0
        bal = await db_session.get(Balance, seed_user.id)
        bal.amount_usd = 0
        await db_session.commit()

        with pytest.raises(HTTPException) as exc:
            await BillingService.precheck_balance(db_session, seed_user.id)
        assert exc.value.status_code == 402


# ── 原子扣费 ───────────────────────────────────────────────────────────────────

class TestDeduct:
    @pytest.mark.asyncio
    async def test_deduct_success(self, db_session, seed_user):
        """正常扣费：余额减少 + 交易记录写入"""
        new_balance = await BillingService.deduct(db_session, seed_user.id, 1.5, "test chat")

        bal = await db_session.get(Balance, seed_user.id)
        assert float(bal.amount_usd) == pytest.approx(98.5)
        assert new_balance == pytest.approx(98.5)

        # 交易记录
        from sqlalchemy import select
        result = await db_session.execute(select(Transaction).where(Transaction.user_id == seed_user.id))
        txn = result.scalars().first()
        assert txn is not None
        assert txn.type == "usage"
        assert float(txn.amount_usd) == pytest.approx(-1.5)
        assert float(txn.balance_after) == pytest.approx(98.5)

    @pytest.mark.asyncio
    async def test_deduct_insufficient(self, db_session, seed_user):
        """余额不足时扣费失败，抛出 402，余额不变"""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await BillingService.deduct(db_session, seed_user.id, 999.0)
        assert exc.value.status_code == 402

        bal = await db_session.get(Balance, seed_user.id)
        assert float(bal.amount_usd) == pytest.approx(100.0)

    @pytest.mark.asyncio
    async def test_deduct_zero_cost(self, db_session, seed_user):
        """零费用不扣费"""
        result = await BillingService.deduct(db_session, seed_user.id, 0.0)
        assert result == 0.0
        bal = await db_session.get(Balance, seed_user.id)
        assert float(bal.amount_usd) == pytest.approx(100.0)


# ── 充值 ───────────────────────────────────────────────────────────────────────

class TestTopup:
    @pytest.mark.asyncio
    async def test_topup(self, db_session, seed_user):
        """充值增加余额并记录流水"""
        new_balance = await BillingService.topup(db_session, seed_user.id, 50.0)
        assert new_balance == pytest.approx(150.0)

        from sqlalchemy import select
        result = await db_session.execute(
            select(Transaction).where(Transaction.user_id == seed_user.id, Transaction.type == "topup")
        )
        txn = result.scalars().first()
        assert txn is not None
        assert float(txn.amount_usd) == pytest.approx(50.0)
