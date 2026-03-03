# ============================================================================
# Leax - Leak Analyzer & eXplorer
# Makefile for installation and development
# ============================================================================

.PHONY: all help install uninstall shell clean

# ============================================================================
# Configuration
# ============================================================================

# Detect OS
OS := $(shell uname -s)

# Auto-detect architecture (ARM64 -> linux/amd64 for Valgrind compatibility)
UNAME_M := $(shell uname -m)
ifeq ($(UNAME_M),arm64)
    PLATFORM := --platform linux/amd64
else
    PLATFORM :=
endif

# ANSI color codes
YELLOW := \033[38;5;214m
LIGHT_YELLOW := \033[38;5;230m
GREEN  := \033[38;5;49m
BLUE   := \033[38;5;211m
RED    := \033[38;5;196m
RESET  := \033[0m

# Linux install paths
LEAX_BIN   := $(HOME)/.local/bin/leax
LEAX_DIR   := $(HOME)/.local/share/leax

# ============================================================================
# Public Commands
# ============================================================================

# Default rule: display help
all: help

# Display available commands
help:
	@echo "Available commands:"
	@echo "  $(BLUE)make install$(RESET)    - Install Leax globally"
	@echo "  $(BLUE)make uninstall$(RESET)  - Remove Leax installation"
ifeq ($(OS),Darwin)
	@echo "  $(BLUE)make shell$(RESET)      - Open interactive shell in Docker container"
	@echo "  $(BLUE)make clean$(RESET)      - Remove Docker image and Python cache"
endif
	@echo ""

# ============================================================================
# Install
# ============================================================================

ifeq ($(OS),Linux)
install: check-deps
	@echo ""
	@printf "$(YELLOW)- $(RESET)Installing Leax sources to $(LEAX_DIR)/srcs/"
	@mkdir -p $(LEAX_DIR)/srcs
	@cp srcs/*.py $(LEAX_DIR)/srcs/
	@cp requirements.txt $(LEAX_DIR)/
	@printf "\r$(GREEN)✓ $(RESET)Installing Leax sources to $(LEAX_DIR)/srcs/\n"
	@printf "$(YELLOW)- $(RESET)Installing Python dependencies"
	@pip3 install --user -r requirements.txt > /dev/null 2>&1
	@printf "\r$(GREEN)✓ $(RESET)Installing Python dependencies\n"
	@printf "$(YELLOW)- $(RESET)Installing Leax to $(LEAX_BIN)"
	@mkdir -p $(HOME)/.local/bin
	@chmod +x leax_cli
	@cp leax_cli $(LEAX_BIN)
	@printf "\r$(GREEN)✓ $(RESET)Installing Leax to $(LEAX_BIN)\n"
	@echo "$(GREEN)✓$(RESET) Installation complete!"
	@echo ""
	@echo "$(YELLOW)-$(RESET) Run $(LIGHT_YELLOW)leax configure$(RESET) to set up your Mistral API key$(RESET)"
	@echo ""
	@echo "$$PATH" | grep -q "$(HOME)/.local/bin" || \
		(echo "$(YELLOW)⚠$(RESET)  ~/.local/bin is not in your PATH" && \
		 echo "$(YELLOW)-$(RESET)  Add this to your shell config (~/.bashrc or ~/.zshrc):" && \
		 echo "" && \
		 echo "    $(LIGHT_YELLOW)export PATH=\"\$$HOME/.local/bin:\$$PATH\"$(RESET)" && \
		 echo "")

check-deps:
	@missing=""; \
	command -v valgrind > /dev/null 2>&1 || missing="$$missing valgrind"; \
	command -v gdb > /dev/null 2>&1 || missing="$$missing gdb"; \
	command -v python3 > /dev/null 2>&1 || missing="$$missing python3"; \
	command -v pip3 > /dev/null 2>&1 || missing="$$missing pip3"; \
	if [ -n "$$missing" ]; then \
		echo ""; \
		echo "$(RED)✗$(RESET) Missing dependencies:$$missing"; \
		echo "$(YELLOW)-$(RESET) Install them with your package manager, e.g.:"; \
		echo "    $(LIGHT_YELLOW)sudo apt install$$missing$(RESET)"; \
		echo ""; \
		exit 1; \
	fi
else
install: build
	@sudo -v
	@printf "$(YELLOW)- $(RESET)Installing Leax to /usr/local/bin"
	@chmod +x leax_cli > /dev/null 2>&1
	@sudo cp leax_cli /usr/local/bin/leax > /dev/null 2>&1
	@printf "\r$(GREEN)✓ $(RESET)Installing Leax to /usr/local/bin\n"
	@echo "$(GREEN)✓$(RESET) Installation complete!"
	@echo ""
	@echo "$(YELLOW)-$(RESET) Run $(LIGHT_YELLOW)leax configure$(RESET) to set up your Mistral API key$(RESET)"
	@echo ""
endif

# ============================================================================
# Uninstall
# ============================================================================

ifeq ($(OS),Linux)
uninstall:
	@echo ""
	@printf "$(YELLOW)- $(RESET)Removing Leax installation"
	@rm -f $(LEAX_BIN)
	@rm -rf $(LEAX_DIR)
	@printf "\r$(GREEN)✓ $(RESET)Removing Leax installation\n"
	@echo ""
else
uninstall:
	@echo ""
	@sudo -v
	@printf "$(YELLOW)- $(RESET)Removing Leax installation"
	@sudo rm -f /usr/local/bin/leax > /dev/null 2>&1
	@printf "\r$(GREEN)✓ $(RESET)Removing Leax installation\n"
	@echo ""
endif

# ============================================================================
# Docker commands (macOS only)
# ============================================================================

# Open interactive shell in container
shell:
ifeq ($(OS),Darwin)
	@docker image inspect leax > /dev/null 2>&1 || $(MAKE) build --no-print-directory
	@docker run $(PLATFORM) -it --rm --cap-add=SYS_PTRACE --security-opt seccomp=unconfined -v $(PWD):/app leax /bin/bash
else
	@echo "$(YELLOW)-$(RESET) 'make shell' is only available on macOS (Docker mode)"
endif

# Remove Docker image and Python cache
clean:
	@echo ""
ifeq ($(OS),Darwin)
	@printf "$(YELLOW)- $(RESET)Removing Docker image"
	@docker rmi leax > /dev/null 2>&1 || true
	@printf "\r$(GREEN)✓ $(RESET)Removing Docker image\n"
endif
	@printf "$(YELLOW)- $(RESET)Cleaning Python cache"
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@printf "\r$(GREEN)✓ $(RESET)Cleaning Python cache\n"
	@echo ""

# ============================================================================
# Internal Commands
# ============================================================================

# Build Docker image (auto-triggered by install/shell if needed, macOS only)
build:
	@echo ""
	@printf "\033[?25l"; \
	YELLOW='\033[38;5;214m'; \
	GREEN='\033[38;5;49m'; \
	WHITE='\033[97m'; \
	DARK_GRAY='\033[38;5;238m'; \
	RESET='\033[0m'; \
	(pos=0; seconds=0; iterations=0; while true; do \
		printf "\r$${YELLOW}- $${RESET}$${WHITE}Building Docker image $${RESET}"; \
		for i in 0 1 2 3; do \
			if [ $$i -eq $$pos ]; then \
				printf "$${GREEN}--$${RESET}"; \
			else \
				printf "$${WHITE}--$${RESET}"; \
			fi; \
		done; \
		printf " $${DARK_GRAY}$${seconds}s$${RESET}"; \
		pos=$$((pos + 1)); \
		if [ $$pos -ge 4 ]; then pos=0; fi; \
		iterations=$$((iterations + 1)); \
		if [ $$((iterations % 10)) -eq 0 ]; then seconds=$$((seconds + 1)); fi; \
		sleep 0.1; \
	done) & \
	SPINNER_PID=$$!; \
	docker build $(PLATFORM) -t leax . > /dev/null 2>&1; \
	BUILD_STATUS=$$?; \
	kill $$SPINNER_PID 2>/dev/null; wait $$SPINNER_PID 2>/dev/null; \
	printf "\r\033[K"; \
	printf "\033[?25h"; \
	GREEN='\033[38;5;49m'; \
	RESET='\033[0m'; \
	RED='\033[38;5;196m'; \
	if [ $$BUILD_STATUS -eq 0 ]; then \
		printf "$${GREEN}✓ $${RESET}Building Docker image\n"; \
	else \
		printf "$${RED}✗ $${RESET}Building Docker image failed\n"; \
		exit 1; \
	fi
