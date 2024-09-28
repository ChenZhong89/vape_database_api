import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from urllib.parse import quote
from playwright.async_api import async_playwright
import agentql
import logging

app = FastAPI()

class ProductBase(BaseModel):
    name: str
    link: str
    img: str

class ProductDetails(ProductBase):
    Battery: Optional[str] = None
    Max_Puff: Optional[str] = None
    Display: Optional[str] = None
    Nicotine: Optional[str] = None
    E_liquid_Capacity: Optional[str] = None

class SearchRequest(BaseModel):
    search_keyword: str
    existing_product_names: List[str] = []

async def fetch_product_details(context, url, product=None):
    page = await agentql.wrap_async(context.new_page())
    await page.goto(url)
    await page.mouse.wheel(0, 3000)
    
    PRODUCT_DETAIL_QUERY = """
    {
        Battery
        Max_Puff
        Display
        Nicotine
        E_liquid_Capacity
    }
    """

    PRODUCT_INFO_QUERY = """
    {
        product_name
        product_link
        product_img
    }
    """
    
    try:
        detail_response = await page.query_data(PRODUCT_DETAIL_QUERY)
        
        if product is None:
            product_info = await page.query_data(PRODUCT_INFO_QUERY)
            product_details = ProductBase(
                name=product_info.get("product_name"),
                link=product_info.get("product_link"),
                img=product_info.get("product_img")
            )
        else:
            product_details = ProductBase(**product.dict())

        return ProductDetails(
            **product_details.dict(),
            **detail_response
        )
    except Exception as e:
        product_name = product.name if product else "Unknown"
        logging.error(f"Error fetching details for product {product_name}: {e}")
        return None

async def get_product_names(context, url):
    page = await agentql.wrap_async(context.new_page())
    await page.goto(url)
    await page.mouse.wheel(0, 4000)
    
    PRODUCT_LIST_QUERY = """
    {
        products(the first 5)[] {
            product_name
            product_link
            product_img
        }
    }
    """
    
    try:
        response = await page.query_data(PRODUCT_LIST_QUERY)
        products = response.get("products", [])
        return [ProductBase(**product) for product in products[:5]]
    except Exception as e:
        logging.error(f"Error fetching products: {e}")
        return None

async def get_names_and_fetch(url, existing_product_names):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, timeout=30000)
        context = await browser.new_context()
        
        trunc_products = await get_product_names(context, url)
        
        new_products = [p for p in trunc_products if p.name not in existing_product_names]
        
        async def fetch_product(product):
            try:
                return await fetch_product_details(context, product.link, product)
            except Exception as e:
                logging.error(f"Error processing product: {e}")
                return None

        tasks = [fetch_product(product) for product in new_products]
        fetched_products = await asyncio.gather(*tasks)
        
        await browser.close()
        return [p for p in fetched_products if p is not None]

async def search_and_scrape(search_keyword, existing_product_names):
    search_url = f"https://demandvape.com/index.php?route=product/search&search={quote(search_keyword)}&category_id=1096"

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, timeout=30000)
        context = await browser.new_context()
        page = await context.new_page()
        
        redirected = False
        final_url = search_url

        async def handle_response(response):
            nonlocal redirected, final_url
            if response.url == search_url and 300 <= response.status < 400:
                redirected = True
                final_url = response.headers.get('location', search_url)

        page.on("response", handle_response)
        
        await page.goto(search_url)
        await page.wait_for_load_state('load')

        if not redirected:
            result = await get_names_and_fetch(search_url, existing_product_names)
        else:
            details = await fetch_product_details(context, final_url)
            result = [details] if details else []

        await browser.close()
        return result

@app.post("/scrape", response_model=List[ProductDetails])
async def scrape_endpoint(url: str, existing_product_names: List[str] = []):
    try:
        result = await get_names_and_fetch(url, existing_product_names)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/search-and-scrape", response_model=List[ProductDetails])
async def search_and_scrape_endpoint(request: SearchRequest):
    try:
        result = await search_and_scrape(request.search_keyword, request.existing_product_names)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)