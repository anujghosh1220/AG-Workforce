import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_current_auth, require_roles
from app.api.pagination import Page, paginate
from app.core.database import get_db
from app.models import KnowledgeCategory, Product, ProductKnowledge, UserRole
from app.schemas import (
    KnowledgeCreate,
    KnowledgeOut,
    KnowledgeUpdate,
    ProductCreate,
    ProductOut,
    ProductUpdate,
)
from app.services.helpers import get_or_404, log_activity
from app.models import ActorType

products_router = APIRouter(prefix="/products", tags=["products"])
knowledge_router = APIRouter(prefix="/knowledge", tags=["knowledge"])

WRITE_ROLES = (UserRole.owner, UserRole.admin, UserRole.member)


# ---------------------------------------------------------- products


@products_router.get("", response_model=Page[ProductOut])
def list_products(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    query = select(Product).where(Product.workspace_id == auth.workspace.id).order_by(Product.name)
    items, total, page, page_size = paginate(db, query, page, page_size)
    return Page.build([ProductOut.model_validate(p) for p in items], total, page, page_size)


@products_router.post("", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
def create_product(
    body: ProductCreate,
    auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
    db: Session = Depends(get_db),
):
    product = Product(workspace_id=auth.workspace.id, **body.model_dump())
    db.add(product)
    db.flush()
    log_activity(db, auth.workspace.id, "product.created", actor_id=auth.user.id,
                 actor_type=ActorType.user, description=f"Product '{product.name}' created")
    db.commit()
    db.refresh(product)
    return ProductOut.model_validate(product)


@products_router.patch("/{product_id}", response_model=ProductOut)
def update_product(
    product_id: uuid.UUID,
    body: ProductUpdate,
    auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
    db: Session = Depends(get_db),
):
    product = get_or_404(db, Product, product_id, auth.workspace.id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(product, field, value)
    db.commit()
    db.refresh(product)
    return ProductOut.model_validate(product)


# ---------------------------------------------------------- knowledge


@knowledge_router.get("", response_model=Page[KnowledgeOut])
def list_knowledge(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    category: KnowledgeCategory | None = None,
    product_id: uuid.UUID | None = None,
    q: str | None = None,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    wid = auth.workspace.id
    query = select(ProductKnowledge).where(ProductKnowledge.workspace_id == wid)
    if category:
        query = query.where(ProductKnowledge.category == category)
    if product_id:
        query = query.where(ProductKnowledge.product_id == product_id)
    if q:
        like = f"%{q.strip()}%"
        query = query.where(
            ProductKnowledge.title.ilike(like) | ProductKnowledge.content.ilike(like)
        )
    query = query.order_by(ProductKnowledge.category, ProductKnowledge.sort_order, ProductKnowledge.title)
    items, total, page, page_size = paginate(db, query, page, page_size)
    return Page.build([KnowledgeOut.model_validate(k) for k in items], total, page, page_size)


@knowledge_router.post("", response_model=KnowledgeOut, status_code=status.HTTP_201_CREATED)
def create_knowledge(
    body: KnowledgeCreate,
    auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
    db: Session = Depends(get_db),
):
    wid = auth.workspace.id
    if body.product_id:
        get_or_404(db, Product, body.product_id, wid)
    item = ProductKnowledge(workspace_id=wid, **body.model_dump())
    db.add(item)
    db.flush()
    log_activity(db, wid, "knowledge.created", actor_id=auth.user.id, actor_type=ActorType.user,
                 description=f"Knowledge '{item.title}' created")
    db.commit()
    db.refresh(item)
    return KnowledgeOut.model_validate(item)


@knowledge_router.patch("/{knowledge_id}", response_model=KnowledgeOut)
def update_knowledge(
    knowledge_id: uuid.UUID,
    body: KnowledgeUpdate,
    auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
    db: Session = Depends(get_db),
):
    wid = auth.workspace.id
    item = get_or_404(db, ProductKnowledge, knowledge_id, wid)
    data = body.model_dump(exclude_unset=True)
    if data.get("product_id"):
        get_or_404(db, Product, data["product_id"], wid)
    for field, value in data.items():
        setattr(item, field, value)
    db.commit()
    db.refresh(item)
    return KnowledgeOut.model_validate(item)
