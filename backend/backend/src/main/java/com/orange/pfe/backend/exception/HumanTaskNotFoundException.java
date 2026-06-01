package com.orange.pfe.backend.exception;

public class HumanTaskNotFoundException extends RuntimeException {

    public HumanTaskNotFoundException(Long id) {
        super("HumanTask not found with id: " + id);
    }
}
