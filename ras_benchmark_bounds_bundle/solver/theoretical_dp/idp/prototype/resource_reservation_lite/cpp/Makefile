CXX ?= g++
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -pedantic

all: reservation_bb

reservation_bb: reservation_bb.cpp
	$(CXX) $(CXXFLAGS) $< -o $@

clean:
	rm -f reservation_bb
