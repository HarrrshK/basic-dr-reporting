from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Doctor, Product, Visit, VisitProduct
from ..schemas import ProductCreate, ProductOut

router = APIRouter(prefix="/products", tags=["products"])


@router.post("", response_model=ProductOut, status_code=201)
def create(payload: ProductCreate, db: Session = Depends(get_db)):
    if db.scalar(select(Product).where(Product.name.ilike(payload.name.strip()))): raise HTTPException(409, "Product already exists")
    product = Product(**payload.model_dump()); db.add(product); db.commit(); db.refresh(product); return product


@router.get("")
def list_products(db: Session = Depends(get_db)):
    rows = db.execute(select(Product.id, Product.name, Product.description, Product.active,
        func.count(func.distinct(VisitProduct.visit_id)).label("visits"), func.count(func.distinct(Visit.doctor_id)).label("doctors"),
        func.count(func.distinct(Doctor.area_id)).label("areas")).select_from(Product).outerjoin(VisitProduct, VisitProduct.product_id == Product.id).outerjoin(Visit, Visit.id == VisitProduct.visit_id).outerjoin(Doctor, Doctor.id == Visit.doctor_id).group_by(Product.id).order_by(Product.name)).all()
    return [dict(row._mapping) for row in rows]
