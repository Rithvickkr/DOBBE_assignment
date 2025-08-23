from langchain_together import ChatTogether
from langchain.agents import create_openai_functions_agent, AgentExecutor
from langchain.tools import Tool
from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder
from dotenv import load_dotenv
import os
import asyncio

load_dotenv()


def calculator_tool(expression: str) -> str:
    """Calculate mathematical expressions safely"""
    try:
        result = eval(expression)
        return str(result)
    except:
        return "Invalid expression"

def code_formatter_tool(code: str) -> str:
    """Format Python code"""
    try:
        import black
        return black.format_str(code, mode=black.FileMode())
    except:
        return f"Formatted code:\n{code}"

# Create tools
tools = [
    Tool(
        name="calculator",
        description="Calculate mathematical expressions",
        func=calculator_tool
    ),
    Tool(
        name="code_formatter", 
        description="Format Python code",
        func=code_formatter_tool
    )
]


llm = ChatTogether(
    together_api_key=os.getenv("TOGETHER_API_KEY"),
    model="lgai/exaone-3-5-32b-instruct"
)


prompt = ChatPromptTemplate.from_messages([
    ("system", "{system_prompt}"),
    ("user", "{input}"),
    MessagesPlaceholder(variable_name="agent_scratchpad"),
])


code_agent_prompt = "You are a helpful coding assistant. Use tools when needed to help with calculations or code formatting."
code_agent = create_openai_functions_agent(llm, tools, prompt)
code_executor = AgentExecutor(agent=code_agent, tools=tools, verbose=True)

doc_agent_prompt = "You are a documentation expert. Help explain code and write clear documentation."
doc_agent = create_openai_functions_agent(llm, tools, prompt)
doc_executor = AgentExecutor(agent=doc_agent, tools=tools, verbose=True)

async def test_agents():
    print("Testing Code Agent:")
    response1 = await code_executor.ainvoke({
        "input": "Write a factorial function and calculate 5!",
        "system_prompt": code_agent_prompt
    })
    print(f"Code Agent: {response1['output']}\n")
    
    print("Testing Documentation Agent:")
    response2 = await doc_executor.ainvoke({
        "input": "Explain what a factorial function does and its use cases",
        "system_prompt": doc_agent_prompt
    })
    print(f"Doc Agent: {response2['output']}")

if __name__ == "__main__":
    asyncio.run(test_agents())
